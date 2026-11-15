"""Checagem de fatos do roteiro: o LLM lista as afirmações de cada segmento com fonte e trecho literal, e o app
confere cada trecho na página.

Origem: skill `estudio-checagem` (procedimento, formato do item `{cena, afirmacao, veredito, fonte, url, citacao,
correcao, nota}`, regras e armadilhas), skill `factcheck-source-retrieval` (trecho de 5 a 30 palavras copiado da
página, relatório corrigido, página viva, atribuição) e `estudio-operacao/references/padrao.md` (regras 2, 3, 3b e 4).
O documento do projeto não muda aqui: as afirmações ficam em `FactClaim`; a correção só entra pelo `apply.py`.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Any

import httpx

from studio.core.project import service
from studio.core.project.document import ProjectDoc, Segment
from studio.db import session_scope
from studio.ids import new_id
from studio.models import FactClaim, Project
from studio.providers import registry
from studio.providers.llm import web_tools_enabled
from studio.workers.factcheck.textify import literal_span, normalize
from studio.workers.factcheck.verify import verify
from studio.workers.pipeline import authoring
from studio.workers.pipeline.build import BuildCancelled

log = logging.getLogger(__name__)

VERDICTS = ("ok", "corrigir", "sem_fonte", "boato")
PARALLEL_CHECKS = 4
EXCERPT_NOT_FOUND = "trecho não achado no roteiro"

FACTCHECK_SYSTEM = """Você é o checador de fatos de um canal do YouTube. Confira cada data, número, nome, preço e citação
do roteiro numa fonte primária. É o que separa o canal de um canal de IA genérico, e vem antes de qualquer gasto com
voz paga.

Para cada afirmação checável de cada segmento, devolva um item com:
- segment_id: o id do segmento (exatamente como veio no pedido);
- scene_id: só quando a afirmação é a citação de uma cena "quote" (o id da cena); senão, vazio;
- excerpt: o trecho LITERAL do segmento que a afirmação cobre, copiado caractere por caractere do texto do segmento;
- claim: a afirmação, curta;
- verdict: "ok" (confere na fonte), "corrigir" (a fonte diz outra coisa), "sem_fonte" (não achou fonte primária) ou
  "boato" (vazamento, "insider", analista ou rumor que o roteiro trata como fato);
- source_title: nome do veículo ou do documento; url: o link da página onde o trecho está;
- quote: trecho literal da página da fonte, de 5 a 30 palavras, COPIADO da página (aspas curvas e retas contam;
  nunca reescreva nem traduza o trecho);
- correction: só quando o veredito não é "ok": o texto novo para o `excerpt` (reescreve só o trecho, no idioma do
  roteiro, no mesmo tom, cabendo na frase do segmento);
- note: o que o roteirista precisa saber (data da fonte, "última atualização", outro número em outra fonte).

Regras do canal:
1. Todo número, data, nome e citação conferidos em fonte primária. O que não tem fonte sai (veredito "sem_fonte",
   com a correção que tira a afirmação ou a deixa sem o detalhe). Imprensa sempre atribuída.
2. Boato é boato: vazamento, "insider" e analista entram marcados, com quem disse e quando. Boato desmentido entra
   como desmentido, com a data do desmentido.
3. Opinião é opinião: a tese do canal sem fonte entra dita como opinião ("na minha opinião"), nunca como fato; os
   fatos que sustentam a opinião vêm com fonte.
4. Acusação é acusação: enquanto houver processo, a pessoa é "acusada"; os detalhes vêm atribuídos a quem afirmou
   (júri, polícia, empresa), com data. Procure a notícia mais recente de cada caso.
5. Preço e data: confira na página oficial, no fuso do mercado do canal (a loja mostra o horário do navegador).
6. Página viva (incidente, placar, cotação): guarde o horário da "última atualização" junto com o número na nota.
7. Empresa de análise (Chainalysis, Elliptic, TRM) não é governo: diga quem atribuiu e com que confiança.
8. Citação em outra língua: use o veículo que ouviu no original; tradução em cadeia muda a frase.
9. Relatório oficial corrigido pode ter número diferente do comunicado original: informe os dois na nota.
10. Nunca invente URL. Sem página que você tenha visto, deixe url e quote vazios e use "sem_fonte".
11. Datas relativas ("ainda não saiu", "daqui a 3 dias") valem para a data de hoje do pedido.

Não liste o que não é checável (transições, perguntas retóricas, frases de efeito). Use as fontes do roteiro
primeiro; quando puder buscar na web, procure a fonte primária de cada afirmação que não tem fonte."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_id": {"type": "string"},
                    "scene_id": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "source_title": {"type": "string"},
                    "url": {"type": "string"},
                    "quote": {"type": "string"},
                    "correction": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["segment_id", "excerpt", "claim", "verdict", "source_title", "url", "quote",
                             "correction", "note"],
            },
        }
    },
    "required": ["items"],
}


class FactcheckError(Exception):
    """Erro com o código HTTP que a rota devolve (mensagem em português)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def segment_hash(text: str) -> str:
    """sha256 do texto do segmento, 16 hex: muda quando o texto muda (afirmação "desatualizada")."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _text(value: object, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _quote_scenes(doc: ProjectDoc, lang: str, segment_ids: set[str]) -> list[dict[str, str]]:
    out = []
    for scene in doc.scenes:
        if scene.kind != "quote" or scene.segment_id not in segment_ids:
            continue
        text = (scene.overlay_text.get(lang) or scene.overlay_text.get(doc.primary_language) or "").strip()
        source = (scene.card.source or "") if scene.card else ""
        if text or source:
            out.append({"id": scene.id, "segment_id": scene.segment_id, "text": text, "source": source.strip()})
    return out


def build_context(doc: ProjectDoc, lang: str, segments: list[Segment]) -> dict[str, Any]:
    ids = {s.id for s in segments}
    return {
        "today": date.today().isoformat(),
        "lang": lang,
        "topic": doc.topic or doc.title,
        "segments": [{"id": s.id, "role": s.role, "chapter": s.chapter or "", "text": s.text.get(lang, "")}
                     for s in segments],
        "sources": [{"n": i, "title": src.title, "url": src.url, "note": src.note}
                    for i, src in enumerate(doc.sources, 1)],
        "quotes": _quote_scenes(doc, lang, ids),
    }


def build_prompt(ctx: dict[str, Any], *, web: bool) -> str:
    lines = [
        f"Data de hoje: {ctx['today']}.",
        f"Idioma do roteiro: {authoring.lang_name(ctx['lang'])} ({ctx['lang']}).",
        f"Tema: {ctx['topic']}.",
        "Você pode buscar na web (WebSearch e WebFetch): abra a página antes de citar o trecho."
        if web else "Sem busca web: use só as fontes do roteiro e as que você conhece com o link exato; na dúvida, "
        "\"sem_fonte\" com url e quote vazios.",
        "",
        "Fontes do roteiro:",
    ]
    if ctx["sources"]:
        for src in ctx["sources"]:
            extra = f" ({src['note']})" if src["note"] else ""
            lines.append(f"[{src['n']}] {src['title']} {src['url']}{extra}".rstrip())
    else:
        lines.append("(nenhuma)")
    if ctx["quotes"]:
        lines += ["", "Cenas de citação (kind quote):"]
        for q in ctx["quotes"]:
            lines.append(f"- cena {q['id']} (segmento {q['segment_id']}): \"{q['text']}\""
                         + (f" | fonte do cartão: {q['source']}" if q["source"] else ""))
    lines += ["", "Segmentos (id | papel | capítulo):"]
    for seg in ctx["segments"]:
        lines.append(f"--- {seg['id']} | {seg['role']} | {seg['chapter'] or '-'}")
        lines.append(seg["text"])
    lines += ["", "Devolva os itens de todas as afirmações checáveis desses segmentos."]
    return "\n".join(lines)


def _clean_url(value: object) -> str:
    url = _text(value, 2000)
    return url if url.lower().startswith(("http://", "https://")) else ""


def clean_url(value: object) -> str:
    """Link aceito numa afirmação: só http(s); qualquer outra coisa vira vazio."""
    return _clean_url(value)


def provider_has_web(provider_id: str) -> bool:
    """Só o Claude Code ganha WebSearch e WebFetch na checagem (e só com `VS_FACTCHECK_WEB` ligado)."""
    return provider_id == "claude_code" and web_tools_enabled()


def _ask_llm(ctx: dict[str, Any], project_id: str, job_id: str | None) -> tuple[Any, str]:
    """Chama a cadeia de LLM montando o prompt para o provedor da vez: quem não tem busca web não lê que pode
    buscar (isso empurraria para link e trecho inventados, contra a regra 10)."""

    def call(provider):
        prompt = build_prompt(ctx, web=provider_has_web(provider.info.id))
        return provider.complete_json(task="factcheck", system=FACTCHECK_SYSTEM, prompt=prompt, schema=SCHEMA,
                                      context=ctx)

    result, provider, _ = registry.run_chain(
        "llm",
        registry.resolve_chain("llm", None),
        call,
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
        project_id=project_id,
        job_id=job_id,
    )
    return result, provider.info.id


def _key(text: str) -> str:
    return normalize(text or "")


def parse_items(data: object, doc: ProjectDoc, lang: str, segment_ids: set[str]) -> list[dict[str, Any]]:
    """Itens válidos do LLM: segmento conhecido, veredito conhecido e `excerpt` literal (ou vazio com nota)."""
    raw = data.get("items") if isinstance(data, dict) else None
    scenes = {s.id: s.segment_id for s in doc.scenes}
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        seg_id = _text(item.get("segment_id"), 32)
        if seg_id not in segment_ids:
            continue
        claim = _text(item.get("claim"), 2000)
        if not claim:
            continue
        verdict = _text(item.get("verdict"), 20).lower()
        text = doc.segment(seg_id).text.get(lang, "")
        excerpt = _text(item.get("excerpt"), 2000)
        note = _text(item.get("note"), 2000)
        span = literal_span(excerpt, text) if excerpt else None
        if span is None:
            if excerpt:
                note = "; ".join(n for n in (EXCERPT_NOT_FOUND, note) if n)
            excerpt = ""
        else:
            excerpt = span
        scene_id = _text(item.get("scene_id"), 32) or None
        out.append({
            "segment_id": seg_id,
            "scene_id": scene_id if scene_id and scenes.get(scene_id) == seg_id else None,
            "excerpt": excerpt,
            "claim": claim,
            "verdict": verdict if verdict in VERDICTS else "sem_fonte",
            "source_title": _text(item.get("source_title"), 300),
            "url": _clean_url(item.get("url")),
            "quote": _text(item.get("quote"), 2000),
            "correction": _text(item.get("correction"), 4000),
            "note": note,
        })
    return out


def check_all(items: list[dict[str, Any]], *, http: httpx.Client | None = None,
              progress: Callable[[int, int], None] | None = None) -> list[tuple[bool | None, str]]:
    """Confere o trecho de cada item na página (até `PARALLEL_CHECKS` ao mesmo tempo), na ordem dos itens."""
    results: list[tuple[bool | None, str]] = [(None, "")] * len(items)
    done = 0

    def one(i: int) -> tuple[int, tuple[bool | None, str]]:
        try:
            return i, verify(items[i]["url"], items[i]["quote"], http=http)
        except Exception as exc:  # noqa: BLE001 - uma página ruim não derruba a checagem
            log.warning("conferência do trecho falhou: %s", exc)
            return i, (None, f"a conferência falhou ({exc})")

    with ThreadPoolExecutor(max_workers=PARALLEL_CHECKS) as pool:
        for i, result in pool.map(one, range(len(items))):
            results[i] = result
            done += 1
            if progress:
                progress(done, len(items))
    return results


def run_factcheck(project_id: str, lang: str | None = None, segment_ids: list[str] | None = None, *,
                  job_id: str | None = None, http: httpx.Client | None = None,
                  progress: Callable[[float, str], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Checa os segmentos (todos, ou `segment_ids`) no idioma e grava as afirmações. Devolve as contagens.

    Cancelado no meio (`cancelled()`), sai sem gravar nada (`BuildCancelled`, que o runner trata como cancelado)."""
    report = progress or (lambda _f, _m: None)

    def stop_if_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise BuildCancelled("checagem cancelada")

    doc = service.get_doc(project_id)
    lang = lang or doc.primary_language
    if lang not in doc.languages:
        raise FactcheckError(400, f"o projeto não tem o idioma {lang}")
    wanted = set(segment_ids or [])
    segments = [s for s in doc.segments if s.text.get(lang, "").strip() and (not wanted or s.id in wanted)]
    if not segments:
        raise FactcheckError(409, "não há texto do roteiro para checar neste idioma")
    report(0.05, "Lendo o roteiro")
    ctx = build_context(doc, lang, segments)
    result, provider_id = _ask_llm(ctx, project_id, job_id)
    web = provider_has_web(provider_id)
    stop_if_cancelled()
    ids = {s.id for s in segments}
    items = parse_items(result.data, doc, lang, ids)
    report(0.6, f"Conferindo {len(items)} trechos nas páginas")
    checks = check_all(items, http=http,
                       progress=lambda d, n: report(0.6 + 0.35 * d / max(n, 1), f"Conferindo {len(items)} trechos nas páginas"))
    stop_if_cancelled()
    now = datetime.now(UTC)
    hashes = {s.id: segment_hash(s.text.get(lang, "")) for s in segments}
    with session_scope() as s:
        # Checagem nova de um segmento substitui as afirmações anteriores dele neste idioma. A decisão de ignorar
        # sobrevive: a mesma afirmação (mesmo segmento, trecho e texto) que o usuário ignorou continua ignorada.
        ignored = {
            (r.segment_id, _key(r.excerpt), _key(r.claim))
            for r in s.query(FactClaim).filter(FactClaim.project_id == project_id, FactClaim.lang == lang,
                                               FactClaim.segment_id.in_(ids), FactClaim.status == "ignorada")
        }
        s.query(FactClaim).filter(FactClaim.project_id == project_id, FactClaim.lang == lang,
                                  FactClaim.segment_id.in_(ids)).delete(synchronize_session=False)

        def status_of(item: dict[str, Any]) -> str:
            key = (item["segment_id"], _key(item["excerpt"]), _key(item["claim"]))
            return "ignorada" if key in ignored else "aberta"

        for item, (found, note) in zip(items, checks, strict=True):
            s.add(FactClaim(id=new_id("fat"), project_id=project_id, run_id=job_id, lang=lang,
                            segment_hash=hashes[item["segment_id"]], quote_found=found, check_note=note,
                            checked_at=now, status=status_of(item), **item))
        row = s.get(Project, project_id)
        if row is not None:
            settings = dict(row.settings or {})
            factcheck = dict(settings.get("factcheck") or {})
            current = dict(factcheck.get(lang) or {})
            checked = {k: v for k, v in dict(current.get("checked") or {}).items()
                       if any(seg.id == k for seg in doc.segments)}
            checked.update(hashes)
            factcheck[lang] = {"checked": checked, "last_job": job_id, "at": now.isoformat(), "web": web}
            settings["factcheck"] = factcheck
            row.settings = settings
    counts = {v: sum(1 for i in items if i["verdict"] == v) for v in VERDICTS}
    return {"claims": len(items), **counts, "quote_missing": sum(1 for f, _ in checks if f is False), "web": web,
            "lang": lang, "provider": provider_id}
