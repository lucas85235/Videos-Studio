"""Checagem das ideias da síntese contra o que o canal já fez e o banco de ideias, e conferência dos links de notícia.

- `repetida`: título com semelhança ≥ 0,75 a um vídeo feito (não vai para o banco, fica só no relatório);
- `parecida`: semelhança de 0,5 a 0,75 com um vídeo feito (vai para o banco, com o aviso no risco);
- `ja_no_banco`: o mesmo título normalizado de uma ideia que já existe no canal, ou semelhança ≥ 0,9 com ela (o mesmo
  título com as palavras em outra ordem, por exemplo); vale também contra as ideias gravadas antes na mesma rodada
  (devolve o id dela);
- `nova`: o resto.

A semelhança de títulos (palavras em comum) não pega o mesmo tema com outro título ("GTA 6 tem português?" x "GTA 6
não terá dublagem"). Por isso a rodada pede também um juízo de tema ao LLM (`judge_duplicates`: uma chamada sem web
para todas as ideias, contra tudo o que o canal já fez, agendou ou tem em produção, o banco de ideias e as ideias
anteriores da mesma rodada) e `merge_verdict` sobe o status: mesmo tema e mesmo ângulo = `repetida` (ou `ja_no_banco`
contra o banco ou uma ideia já gravada da rodada); mesmo tema com ângulo novo = `parecida`, com a diferença dita pelo
modelo. A única descida: a `parecida` só por título que o juízo, perguntado sobre aquele feito ("[suspeita: F#]"),
disse que não é o mesmo tema volta a `nova` (`repetida` e `ja_no_banco` nunca descem). Se o juízo falhar, vale só a
semelhança de títulos.

`done_titles` lê o canal inteiro: o inventário de uploads (`analytics/uploads.py`, com privados e agendados), os
vídeos que o Vulto conhece, os projetos, as ideias feitas/em produção e as fontes externas da config
(`extra_sources`: o banco do sistema antigo `estudio.db`).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from studio.config import get_settings
from studio.db import session_scope
from studio.media import safe_http
from studio.media.novelty import title_similarity
from studio.models import Channel, Idea, Project
from studio.workers.business.ideas import norm
from studio.workers.pipeline.schemas import S, _obj

log = logging.getLogger(__name__)

REPEATED = 0.75
BANK_REPEATED = 0.9  # mais alto que REPEATED: títulos curtos do mesmo nicho dividem muitas palavras
SIMILAR = 0.5
DONE_STATUSES = ("feita", "em_producao")
OPEN_STATUSES = ("nova", "aprovada")
VALIDATE_WORKERS = 6
VALIDATE_BUDGET_S = 60.0
PAGE_BYTES = 64_000
JUDGE_TASK = "idea_dupes"
JUDGE_MAX_DONE = 400  # itens feitos mandados ao juízo (o canal inteiro cabe; acima disso, os mais recentes)
THEME_MAX = 160
PRIVACY_LABELS = {"public": "público", "private": "privado ou agendado", "unlisted": "não listado"}
LEGACY_VIDEO_SKIP = ("cancelado",)
LEGACY_IDEA_STATUSES = ("nova", "em-producao", "feita")
REF_RE = re.compile(r"^([FBI])(\d+)$")


def _legacy_titles(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Vídeos (menos os cancelados) e ideias abertas, em produção ou feitas de um canal do sistema antigo
    (`estudio.db`), lidos só para leitura."""
    path = Path(str(source.get("path") or ""))
    if not path.is_file():
        raise FileNotFoundError(f"banco do sistema antigo não encontrado: {path}")
    canal = str(source.get("canal") or "")
    out: list[dict[str, Any]] = []
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        for titulo, tema, etapa, youtube_id in con.execute(
                "select titulo, tema, etapa, youtube_id from videos where canal = ? and status not in (?)",
                (canal, *LEGACY_VIDEO_SKIP)):
            theme = "" if str(tema or "").startswith("http") else str(tema or "")
            out.append({"title": titulo, "url": f"https://youtu.be/{youtube_id}" if youtube_id else "",
                        "status": f"sistema antigo: {etapa or 'em produção'}", "theme": theme})
        marks = ",".join("?" * len(LEGACY_IDEA_STATUSES))
        for titulo, angulo, status in con.execute(
                f"select titulo, angulo, status from ideias where canal = ? and status in ({marks})",  # noqa: S608
                (canal, *LEGACY_IDEA_STATUSES)):
            out.append({"title": titulo, "url": "", "status": f"ideia do sistema antigo ({status})",
                        "theme": str(angulo or "")})
    finally:
        con.close()
    return out


def done_titles(channel_id: str, *, http: httpx.Client | None = None, now: datetime | None = None,
                gaps: list[str] | None = None, extra_sources: list[dict[str, Any]] | None = None,
                inventory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """O que o canal já fez, agendou ou tem em produção: `[{title, url, status, theme}]`. Fontes: o inventário de
    uploads (o canal inteiro, com privados e agendados; sem o login do canal, a vitrine dos recentes), os vídeos que o
    Vulto conhece, os projetos, as ideias feitas/em produção e `extra_sources` (sistema antigo). O que não deu para
    ler vira uma linha em `gaps`."""
    from studio.workers.analytics import uploads
    from studio.workers.analytics.variables import load_videos

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    gaps = gaps if gaps is not None else []

    def add(title: str | None, url: str = "", status: str = "", theme: str = "") -> None:
        key = norm(title or "")
        if key and key not in seen:
            seen.add(key)
            out.append({"title": title, "url": url, "status": status,
                        "theme": " ".join(str(theme or "").split())[:THEME_MAX]})

    try:
        inv = inventory if inventory is not None else uploads.inventory(channel_id, http, now=now)
    except Exception as exc:  # noqa: BLE001 - sem o inventário, ficam as outras fontes
        inv = {"items": [], "completo": False, "erro": str(exc)}
    if not inv.get("completo"):
        gaps.append("checagem de repetição sem o canal inteiro (privados, agendados e vídeos antigos): "
                    f"{inv.get('erro') or 'inventário de uploads não lido'}")
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        recent = (((channel.settings or {}).get("youtube") or {}).get("recent_videos") or []) if channel else []
        projects = [(p.title, p.status) for p in s.query(Project).filter(Project.channel_id == channel_id)
                    .order_by(Project.created_at.desc()).all()]
        ideas = [(i.title, i.status, i.angle) for i in s.query(Idea).filter(
            Idea.channel_id == channel_id, Idea.status.in_(DONE_STATUSES)).all()]
    for v in inv.get("items") or []:
        add(v.get("title"), f"https://youtu.be/{v['video_id']}" if v.get("video_id") else "",
            PRIVACY_LABELS.get(v.get("privacy") or "", "no canal"), v.get("description") or "")
    for v in recent:
        if isinstance(v, dict):
            add(v.get("title"), f"https://youtu.be/{v['video_id']}" if v.get("video_id") else "", "no canal")
    try:
        for v in load_videos(channel_id):
            add(v.get("title"), f"https://youtu.be/{v['youtube_id']}", "no canal")
    except Exception as exc:  # noqa: BLE001 - sem métricas, ficam as outras fontes
        log.debug("vídeos do canal %s não lidos: %s", channel_id, exc)
    for title, status in projects:
        add(title, "", f"projeto ({status})")
    for title, status, angle in ideas:
        add(title, "", f"ideia ({status.replace('_', ' ')})", angle or "")
    for source in extra_sources or []:
        try:
            for item in _legacy_titles(source):
                add(item["title"], item["url"], item["status"], item["theme"])
        except Exception as exc:  # noqa: BLE001 - fonte externa fora do ar não derruba a rodada
            gaps.append(f"fonte externa de vídeos feitos não lida ({source.get('path')}): {exc}")
    return out


def open_ideas(channel_id: str) -> list[dict[str, Any]]:
    """Todas as ideias do canal (para o "já no banco") com o status."""
    with session_scope() as s:
        rows = s.query(Idea).filter(Idea.channel_id == channel_id).order_by(Idea.created_at.desc()).all()
        return [{"id": r.id, "title": r.title, "status": r.status} for r in rows]


def check_idea(idea: dict[str, Any], done: list[dict[str, Any]], existing: list[dict[str, Any]]) -> dict[str, Any]:
    """`{status, similar_to: {title, url}|None, similarity, idea_id}` (ver o topo do módulo)."""
    key = norm(idea["title"])
    for row in existing:
        if norm(row["title"]) == key:
            return {"status": "ja_no_banco", "similar_to": {"title": row["title"], "url": ""}, "similarity": 1.0,
                    "idea_id": row["id"]}
    bank_best, bank_match = 0.0, None
    for row in existing:
        sim = title_similarity(idea["title"], row["title"])
        if sim > bank_best:
            bank_best, bank_match = sim, row
    if bank_match is not None and bank_best >= BANK_REPEATED:
        return {"status": "ja_no_banco", "similar_to": {"title": bank_match["title"], "url": ""},
                "similarity": round(bank_best, 2), "idea_id": bank_match["id"]}
    best, match = 0.0, None
    for row in done:
        sim = title_similarity(idea["title"], row["title"])
        if sim > best:
            best, match = sim, row
    similar = {"title": match["title"], "url": match.get("url") or ""} if match else None
    if best >= REPEATED:
        return {"status": "repetida", "similar_to": similar, "similarity": round(best, 2), "idea_id": None}
    if best >= SIMILAR:
        return {"status": "parecida", "similar_to": similar, "similarity": round(best, 2), "idea_id": None}
    return {"status": "nova", "similar_to": None, "similarity": round(best, 2), "idea_id": None}


JUDGE_SYSTEM = """Você confere se ideias novas de vídeo repetem o que o canal do YouTube já fez, já agendou, tem em
produção ou já tem no banco de ideias.

Para cada ideia (I1, I2...), compare com FEITOS (F1...: publicados, privados, agendados, em produção), com o BANCO
(B1...: ideias abertas) e com as ideias ANTERIORES da mesma lista (só I com número menor).
- mesmo_tema: o assunto central é o mesmo (o mesmo fato, número, produto, evento ou pergunta), mesmo com outro
  título, outras palavras ou sinônimos (dublagem/dublado, 210/212 GB, GOTY/Jogo do Ano).
- mesmo_angulo: além do tema, a promessa ao espectador é a mesma (responde a mesma pergunta, do mesmo jeito).
Devolva só as ideias que têm par: idea = o ref da ideia, match = o ref do item mais parecido (F#, B# ou I#),
diferenca = em uma frase o que a ideia traz de novo em relação ao par (vazio se nada). Tema vizinho do mesmo nicho
NÃO é mesmo tema. Ideia marcada com [suspeita: F#] tem título parecido com esse F#: devolva sempre a linha dela com
esse match, dizendo se é ou não o mesmo tema. Não invente refs. Escreva em português do Brasil."""


def judge_schema() -> dict[str, Any]:
    flag = {"type": "boolean"}
    return _obj({"matches": {"type": "array", "items": _obj({
        "idea": S, "match": S, "mesmo_tema": flag, "mesmo_angulo": flag, "diferenca": S})}})


def _suspects(ideas: list[dict[str, Any]], done: list[dict[str, Any]]) -> dict[int, int]:
    """Ideia → índice do feito que a semelhança de títulos marcou como `parecida` (o juízo confirma ou descarta)."""
    index = {d["title"]: n for n, d in enumerate(done)}
    out: dict[int, int] = {}
    for n, idea in enumerate(ideas):
        chk = check_idea(idea, done, [])
        if chk["status"] == "parecida" and (chk["similar_to"] or {}).get("title") in index:
            out[n] = index[chk["similar_to"]["title"]]
    return out


def judge_prompt(ideas: list[dict[str, Any]], done: list[dict[str, Any]], bank: list[dict[str, Any]],
                 suspects: dict[int, int] | None = None) -> str:
    def line(ref: str, title: str, *extra: str) -> str:
        return " — ".join([f"{ref}: {title}", *(e for e in extra if e)])

    suspects = suspects or {}
    rows = ["IDEIAS:"] + [line(f"I{n}", i["title"], i.get("angle") or "",
                               f"[suspeita: F{suspects[n - 1] + 1}]" if n - 1 in suspects else "")
                          for n, i in enumerate(ideas, start=1)]
    rows += ["", "FEITOS:"] + [line(f"F{n}", d["title"], d.get("theme") or "", d.get("status") or "")
                               for n, d in enumerate(done[:JUDGE_MAX_DONE], start=1)]
    rows += ["", "BANCO:"] + [line(f"B{n}", b["title"], b.get("status") or "") for n, b in enumerate(bank, start=1)]
    return "\n".join(rows)


def _judge_llm(prompt: str, context: dict[str, Any], job_id: str | None) -> Any:
    """A chamada ao LLM (sem web), pela mesma cadeia das outras tarefas. Os testes trocam por uma resposta pronta."""
    from studio.providers import registry

    result, _, _ = registry.run_chain(
        "llm", registry.resolve_chain("llm", None),
        lambda p: p.complete_json(task=JUDGE_TASK, system=JUDGE_SYSTEM, prompt=prompt, schema=judge_schema(),
                                  max_tokens=8000, context=context),
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd), job_id=job_id)
    return result.data


def judge_duplicates(ideas: list[dict[str, Any]], done: list[dict[str, Any]], bank: list[dict[str, Any]], *,
                     job_id: str | None = None) -> dict[int, dict[str, Any]]:
    """Juízo de tema do LLM: `{índice da ideia: {onde: "feito"|"banco"|"rodada", alvo, mesmo_tema, mesmo_angulo,
    diferenca}}`, só das ideias com par (ref inválido ou ideia apontando para ela mesma ou uma posterior é ignorado).
    Erro do provedor sobe (a rodada vira lacuna e fica a semelhança de títulos)."""
    if not ideas:
        return {}
    done = done[:JUDGE_MAX_DONE]
    suspects = _suspects(ideas, done)
    data = _judge_llm(judge_prompt(ideas, done, bank, suspects),
                      {"ideas": [i["title"] for i in ideas], "done": [d["title"] for d in done],
                       "bank": [b["title"] for b in bank]}, job_id)
    out: dict[int, dict[str, Any]] = {}
    matches = data.get("matches") if isinstance(data, dict) else None
    for m in matches if isinstance(matches, list) else []:
        if not isinstance(m, dict):
            continue
        idea = REF_RE.match(str(m.get("idea") or "").strip().upper())
        match = REF_RE.match(str(m.get("match") or "").strip().upper())
        if not idea or not match or idea.group(1) != "I":
            continue
        i, j = int(idea.group(2)) - 1, int(match.group(2)) - 1
        kind = match.group(1)
        pool = {"F": done, "B": bank, "I": ideas[:i]}[kind]
        if not 0 <= i < len(ideas) or not 0 <= j < len(pool) or i in out:
            continue
        # "não é o mesmo tema" só vale como resposta a uma suspeita (descarta a semelhança de títulos)
        if not m.get("mesmo_tema") and not (kind == "F" and suspects.get(i) == j):
            continue
        out[i] = {"onde": {"F": "feito", "B": "banco", "I": "rodada"}[kind], "alvo": j,
                  "mesmo_tema": bool(m.get("mesmo_tema")), "mesmo_angulo": bool(m.get("mesmo_angulo")),
                  "diferenca": " ".join(str(m.get("diferenca") or "").split())[:300]}
    return out


def merge_verdict(chk: dict[str, Any], verdict: dict[str, Any] | None, *, done: list[dict[str, Any]],
                  bank: list[dict[str, Any]], ideas: list[dict[str, Any]]) -> dict[str, Any]:
    """Junta o juízo de tema à semelhança de títulos. Sobe o status; desce só a `parecida` que o juízo, perguntado
    sobre aquele mesmo feito, disse que não é o mesmo tema (repetida e já no banco nunca descem).
    `ideas`: as ideias da rodada na ordem do juízo (a de `alvo` já passou pela checagem e pode ter sido gravada)."""
    if not verdict or chk["status"] in ("repetida", "ja_no_banco"):
        return chk
    if not verdict["mesmo_tema"]:
        if chk["status"] == "parecida" and verdict["onde"] == "feito":
            return {**chk, "status": "nova", "similar_to": None, "por_tema": True,
                    "titulo_parecido": done[verdict["alvo"]]["title"]}
        return chk
    same = verdict["mesmo_angulo"]
    base = {**chk, "por_tema": True, "onde": verdict["onde"], "diferenca": verdict["diferenca"]}
    if verdict["onde"] == "feito":
        d = done[verdict["alvo"]]
        status = "repetida" if same else "parecida"
        if chk["status"] == "parecida" and status == "parecida":
            return {**chk, "diferenca": verdict["diferenca"]}
        return {**base, "status": status, "similar_to": {"title": d["title"], "url": d.get("url") or ""},
                "idea_id": None}
    if verdict["onde"] == "banco":
        b = bank[verdict["alvo"]]
        if same:
            return {**base, "status": "ja_no_banco", "similar_to": {"title": b["title"], "url": ""},
                    "idea_id": b["id"]}
        return {**base, "status": "parecida", "similar_to": {"title": b["title"], "url": ""}, "idea_id": None}
    other = ideas[verdict["alvo"]]
    other_id = (other.get("check") or {}).get("idea_id") if (other.get("check") or {}).get("saved") else None
    if same and other_id:
        return {**base, "status": "ja_no_banco", "similar_to": {"title": other["title"], "url": ""},
                "idea_id": other_id}
    return {**base, "status": "parecida", "similar_to": {"title": other["title"], "url": ""}, "idea_id": None}


def _url_ok(url: str, http: httpx.Client) -> bool:
    try:
        safe_http.fetch(url, max_bytes=PAGE_BYTES, accept=safe_http.ACCEPT_HTML, http=http, truncate=True)
    except safe_http.FetchError:
        return False
    except Exception as exc:  # noqa: BLE001 - um link ruim nunca derruba a rodada
        log.debug("link %s não conferido: %s", url, exc)
        return False
    return True


def validate_urls(ideas: list[dict[str, Any]], *, http: httpx.Client | None = None,
                  budget_s: float = VALIDATE_BUDGET_S, workers: int = VALIDATE_WORKERS) -> list[str]:
    """Confere por HTTP os links "web" (notícias) das evidências e tira os que não abrem (ou que não responderam no
    tempo). Os links de sinais já vêm do YouTube. Devolve os links removidos. No modo offline sem cliente, não confere
    (nada sai para a rede) e os links ficam."""
    urls = list(dict.fromkeys(ev["url"] for i in ideas for ev in i["evidence"] if not ev.get("checked")))
    if not urls or (http is None and get_settings().offline):
        return []
    ok: set[str] = set()
    client = http or safe_http.new_client()
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls))))
    try:
        futures = {pool.submit(_url_ok, u, client): u for u in urls}
        deadline = time.monotonic() + budget_s
        pending = set(futures)
        while pending:
            left = deadline - time.monotonic()
            if left <= 0:
                log.warning("conferência dos links parou no tempo (%d sem resposta)", len(pending))
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for future in done:
                if future.result():
                    ok.add(futures[future])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if http is None:
            client.close()
    removed = [u for u in urls if u not in ok]
    for idea in ideas:
        idea["evidence"] = [ev for ev in idea["evidence"] if ev.get("checked") or ev["url"] in ok]
        for ev in idea["evidence"]:
            ev["checked"] = True
    return removed
