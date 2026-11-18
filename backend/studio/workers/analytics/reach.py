"""Impressões e CTR da miniatura pela YouTube Reporting API (relatório `channel_reach_basic_a1`).

Por que a Reporting API e não o YouTube Analytics: a lista de métricas do Analytics (`reports.query`, conferida em
15/11/2026 em developers.google.com/youtube/analytics/metrics) não tem impressões nem CTR da miniatura. Elas saem só
pela Reporting API (novidade de 15/01/2026): relatórios diários em CSV com as dimensões `date, channel_id, video_id` e as
métricas `video_thumbnail_impressions` e `video_thumbnail_impressions_ctr`. Usa o mesmo escopo
`yt-analytics.readonly` que a coleta já pede (nada a vincular de novo).

Como funciona:
- a primeira coleta cria o "job" do relatório (uma vez por canal). O Google gera o relatório de cada dia 2 dias depois
  e um histórico dos 30 dias anteriores ao job; até lá o estado fica "esperando";
- cada coleta baixa só os relatórios criados depois do último lido (`createdAfter`);
- um relatório refeito pelo Google (mesmo dia, id novo) substitui o dia inteiro: nada conta duas vezes;
- dias com mais de `KEEP_DAYS` viram um total por vídeo (`base`) para o JSON não crescer sem fim; relatório de um dia
  já somado ao total é ignorado;
- o CTR do CSV é "percentual"; a documentação não diz se vem 0–1 ou 0–100. Por isso cada dia guarda impressões e
  "impressões × CTR cru", e a escala é decidida por canal, olhando todos os relatórios: basta um CTR acima de 1 em
  qualquer relatório para o canal passar a ler 0–100 de vez (`ctr_percent`); senão, fração (0,045 = 4,5%). Assim um dia
  de canal pequeno com todos os CTRs abaixo de 1% não vira 100 vezes mais cliques. Os totais saem sempre em % (0–100);
- os totais começam no histórico do job (30 dias antes de criar o relatório): num vídeo mais antigo que isso, não são
  os da vida inteira do vídeo. `first_week` dá as impressões dos 7 primeiros dias só quando esses dias estão todos
  guardados.

Estado guardado em `Channel.settings["analytics_reach"]` (`format`, `job_id`, `after`, `days`, `base`, `folded_until`,
`ctr_percent`).
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from studio.db import session_scope
from studio.models import Channel
from studio.workers.publish import youtube as yt

REPORTING_API = "https://youtubereporting.googleapis.com/v1"
REPORT_TYPE = "channel_reach_basic_a1"
JOB_NAME = "Vulto: impressões e CTR"
KEEP_DAYS = 45
SETTINGS_KEY = "analytics_reach"
FORMAT = 2  # dias com CTR cru (escala decidida por canal); dados de antes são baixados de novo
FIRST_WEEK_DAYS = 7
log = logging.getLogger(__name__)
# Estados mostrados em Desempenho.
OK, ESPERANDO, SEM_ESCOPO, ERRO = "ok", "esperando", "sem_escopo", "erro"


class ReachScopeMissing(Exception):
    """O token não tem o escopo do Analytics (a Reporting API usa o mesmo)."""


class ReachApiDisabled(yt.YouTubeError):
    """A YouTube Reporting API está desligada no projeto do Google Cloud do OAuth: o job não pode ser criado até
    alguém ativá-la no console (nenhum código resolve isso; a coleta tenta de novo a cada rodada)."""


# Motivos do erro do Google que querem dizer "o token não tem o escopo" (vincular de novo resolve) e "a API está
# desligada no projeto do OAuth" (ativar no Google Cloud resolve; vincular de novo não adianta).
_SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
_DISABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED"}
_CONSOLE = "https://console.developers.google.com/apis/api/youtubereporting.googleapis.com/overview"


def _error_info(resp: httpx.Response) -> tuple[set[str], dict[str, str]]:
    """`reason` de `error.errors[]` e `error.details[]` + `error.status`, e o `metadata` do `ErrorInfo` (com o
    `activationUrl` e o `consumer` quando a API está desligada). Lê o corpo inteiro: a mensagem do Google passa de
    300 caracteres e o motivo vem depois dela (por isso o `SERVICE_DISABLED` não era reconhecido)."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        return set(), {}
    if not isinstance(err, dict):
        return set(), {}
    reasons = {str(e.get("reason")) for e in err.get("errors") or [] if isinstance(e, dict) and e.get("reason")}
    meta: dict[str, str] = {}
    for d in err.get("details") or []:
        if isinstance(d, dict):
            if d.get("reason"):
                reasons.add(str(d["reason"]))
            if isinstance(d.get("metadata"), dict):
                meta.update({str(k): str(v) for k, v in d["metadata"].items()})
    if err.get("status"):
        reasons.add(str(err["status"]))
    return reasons, meta


def _project_of(resp: httpx.Response, meta: dict[str, str]) -> str:
    """Número do projeto do Google Cloud do OAuth (do `consumer` ou da mensagem "... in project 123 ...")."""
    consumer = meta.get("consumer") or ""
    if consumer.startswith("projects/"):
        return consumer.split("/", 1)[1]
    match = re.search(r"project[= ](\d+)", resp.text)
    return match.group(1) if match else ""


def disabled_message(project: str = "", url: str = "") -> str:
    """Mensagem curta (a tela mostra 160 caracteres) com o que fazer quando a Reporting API está desligada."""
    where = f" (projeto {project})" if project else ""
    link = url or (f"{_CONSOLE}?project={project}" if project else _CONSOLE)
    return (f"ative a YouTube Reporting API no Google Cloud{where}; a próxima coleta cria o relatório de impressões"
            f" sozinha. Link: {link}")


def _error(resp: httpx.Response) -> Exception:
    text = resp.text[:300]
    reasons, meta = _error_info(resp)
    body = resp.text
    if reasons & _DISABLED_REASONS or "has not been used in project" in body or "it is disabled" in body:
        return ReachApiDisabled(disabled_message(_project_of(resp, meta), meta.get("activationUrl", "")))
    if resp.status_code == 401 or reasons & _SCOPE_REASONS or any(r in body for r in _SCOPE_REASONS):
        return ReachScopeMissing(text)
    return yt.YouTubeError(f"erro da YouTube Reporting API ({resp.status_code}): {text}")


def _call(http: httpx.Client, method: str, url: str, token: str, **kwargs: Any) -> httpx.Response:
    try:
        resp = http.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
    except httpx.HTTPError as exc:
        raise yt.YouTubeError(f"sem resposta da YouTube Reporting API: {exc}") from exc
    if resp.status_code != 200:
        raise _error(resp)
    return resp


def ensure_job(http: httpx.Client, token: str, job_id: str | None) -> tuple[str, bool]:
    """Id do job do relatório de alcance (acha um que já existe ou cria). Devolve (id, criou agora?)."""
    if job_id:
        return job_id, False
    data = _call(http, "GET", f"{REPORTING_API}/jobs", token).json()
    for job in data.get("jobs") or []:
        if job.get("reportTypeId") == REPORT_TYPE and job.get("id"):
            return str(job["id"]), False
    made = _call(http, "POST", f"{REPORTING_API}/jobs", token,
                 json={"reportTypeId": REPORT_TYPE, "name": JOB_NAME}).json()
    if not made.get("id"):
        raise yt.YouTubeError("a YouTube Reporting API não devolveu o id do relatório criado")
    return str(made["id"]), True


def list_reports(http: httpx.Client, token: str, job_id: str, after: str | None) -> list[dict[str, Any]]:
    """Relatórios do job criados depois de `after` (todas as páginas), do mais antigo ao mais novo."""
    out: list[dict[str, Any]] = []
    page = ""
    for _ in range(50):  # trava contra paginação sem fim
        params: dict[str, str] = {}
        if after:
            params["createdAfter"] = after
        if page:
            params["pageToken"] = page
        data = _call(http, "GET", f"{REPORTING_API}/jobs/{job_id}/reports", token, params=params).json()
        out.extend(r for r in data.get("reports") or [] if isinstance(r, dict))
        page = data.get("nextPageToken") or ""
        if not page:
            break
    return sorted(out, key=lambda r: str(r.get("createTime") or ""))


def _safe_download_url(url: str) -> bool:
    """O token só vai para o próprio Google (o `downloadUrl` vem da API, mas não custa conferir)."""
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "youtubereporting.googleapis.com"


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_report(text: str) -> tuple[dict[str, dict[str, list[float]]], float]:
    """CSV do relatório → (`{dia AAAAMMDD: {vídeo: [impressões, impressões × CTR cru]}}`, maior CTR cru do arquivo).

    O CTR fica cru (sem decidir a escala): quem decide é o canal, em `totals`. Colunas achadas pelo cabeçalho."""
    rows = list(csv.DictReader(io.StringIO(text)))
    out: dict[str, dict[str, list[float]]] = {}
    top = 0.0
    for r in rows:
        day, vid = (r.get("date") or "").strip(), (r.get("video_id") or "").strip()
        impressions = _num(r.get("video_thumbnail_impressions"))
        if not day or not vid or impressions is None:
            continue
        ctr = _num(r.get("video_thumbnail_impressions_ctr")) or 0.0
        top = max(top, ctr)
        clicks = impressions * ctr
        cur = out.setdefault(day, {}).setdefault(vid, [0.0, 0.0])
        cur[0] += impressions
        cur[1] += clicks
    return out, top


def _report_days(report: dict[str, Any], parsed: dict[str, dict[str, list[float]]]) -> set[str]:
    """Dias que o relatório cobre: os do CSV e o do `startTime` (relatório vazio também zera o dia)."""
    days = set(parsed)
    start = str(report.get("startTime") or "")
    if len(start) >= 10:
        days.add(start[:10].replace("-", ""))
    return days


def _load(channel_id: str) -> dict[str, Any]:
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        data = dict((channel.settings or {}).get(SETTINGS_KEY) or {}) if channel else {}
    if data.get("format") != FORMAT:
        # Dias gravados no formato antigo (escala decidida por arquivo): baixa tudo de novo; o job continua.
        data = {"job_id": data["job_id"]} if data.get("job_id") else {}
        data["format"] = FORMAT
    return data


def _store(channel_id: str, data: dict[str, Any]) -> None:
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            return
        settings = dict(channel.settings or {})
        settings[SETTINGS_KEY] = data
        channel.settings = settings  # JSON novo inteiro: o SQLAlchemy só vê a troca


def _fold(data: dict[str, Any], now: datetime) -> None:
    """Dias velhos viram total por vídeo (`base`)."""
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    days: dict[str, Any] = data.setdefault("days", {})
    base: dict[str, list[float]] = data.setdefault("base", {})
    for day in sorted(d for d in days if d < cutoff):
        for vid, (imp, clk) in days.pop(day).items():
            cur = base.setdefault(vid, [0.0, 0.0])
            cur[0] += imp
            cur[1] += clk
        data["folded_until"] = max(day, data.get("folded_until") or "")


def _scale(data: dict[str, Any]) -> float:
    """Quanto multiplicar "impressões × CTR cru ÷ impressões" para chegar em %: 1 se o canal manda 0–100, 100 se fração."""
    return 1.0 if data.get("ctr_percent") else 100.0


def totals(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Impressões e CTR (%) por vídeo, somando o total antigo e os dias guardados (escala do canal)."""
    scale = _scale(data)
    acc: dict[str, list[float]] = {vid: list(v) for vid, v in (data.get("base") or {}).items()}
    for per_video in (data.get("days") or {}).values():
        for vid, (imp, clk) in per_video.items():
            cur = acc.setdefault(vid, [0.0, 0.0])
            cur[0] += imp
            cur[1] += clk
    return {vid: {"impressions": int(round(imp)), "ctr": round(clk / imp * scale, 2) if imp else None}
            for vid, (imp, clk) in acc.items()}


def first_week(data: dict[str, Any], published: dict[str, date]) -> dict[str, float]:
    """Impressões dos 7 primeiros dias de cada vídeo (dia da publicação e os 6 seguintes).

    Só entra o vídeo com os 7 dias todos guardados: vídeo publicado antes do histórico do job (ou com dias já somados
    ao total antigo) ou com menos de 7 dias de relatório fica de fora."""
    days: dict[str, dict[str, list[float]]] = data.get("days") or {}
    out: dict[str, float] = {}
    for vid, start in published.items():
        keys = [(start + timedelta(days=i)).strftime("%Y%m%d") for i in range(FIRST_WEEK_DAYS)]
        if all(k in days for k in keys):
            out[vid] = sum(float((days[k].get(vid) or [0.0])[0]) for k in keys)
    return out


def fetch_reach(channel_id: str, http: httpx.Client, token: str, now: datetime | None = None
                ) -> tuple[str, dict[str, dict[str, float]], str]:
    """Atualiza os dados de alcance do canal. Devolve (estado, totais por vídeo, mensagem).

    Erros viram estado (nunca exceção): a coleta de views segue mesmo se o alcance falhar.
    """
    now = now or datetime.now(UTC)
    data = _load(channel_id)
    try:
        job_id, created = ensure_job(http, token, data.get("job_id"))
        data["job_id"] = job_id
        reports = list_reports(http, token, job_id, data.get("after"))
        for report in reports:
            url = str(report.get("downloadUrl") or "")
            if not _safe_download_url(url):
                continue
            parsed, top = parse_report(_call(http, "GET", url, token).text)
            if top > 1:
                data["ctr_percent"] = True  # de vez: um CTR acima de 1 só existe na escala 0–100
            days: dict[str, Any] = data.setdefault("days", {})
            for day in _report_days(report, parsed):
                if day <= (data.get("folded_until") or ""):
                    continue  # já está no total antigo
                days[day] = parsed.get(day, {})  # relatório refeito substitui o dia inteiro
            created_at = str(report.get("createTime") or "")
            if created_at > (data.get("after") or ""):
                data["after"] = created_at
        _fold(data, now)
    except ReachScopeMissing as exc:
        _store(channel_id, data)
        return SEM_ESCOPO, {}, str(exc)
    except (yt.YouTubeError, ValueError, csv.Error) as exc:
        _store(channel_id, data)
        return ERRO, {}, str(exc)
    except Exception as exc:  # noqa: BLE001 - resposta estranha do Google não derruba a coleta das views
        log.warning("impressões do canal %s falharam: %s", channel_id, exc)
        _store(channel_id, data)
        return ERRO, {}, f"resposta inesperada da YouTube Reporting API: {exc}"
    _store(channel_id, data)
    found = totals(data)
    if not found:
        msg = ("relatório de impressões criado agora; os primeiros números chegam em até 2 dias" if created
               else "o YouTube ainda não entregou o relatório de impressões (leva até 2 dias)")
        return ESPERANDO, {}, msg
    return OK, found, ""
