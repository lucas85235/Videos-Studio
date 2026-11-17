"""Síntese da pesquisa de vídeos: uma chamada ao Claude Code (com WebSearch e WebFetch) junta os sinais coletados,
notícias, datas e tendências e devolve de 10 a 25 ideias ranqueadas, cada uma com data de publicação.

Os números e links nunca vêm do modelo: cada evidência cita o `ref` de um sinal (P# próprio canal, C# concorrente,
K# busca, M# comentário) e o Python troca o `ref` pela URL real (`postprocess`); `ref` que não existe é descartado.
Link de notícia (evidência "web") é conferido por HTTP depois (`check.validate_urls`).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from studio.config import get_settings
from studio.providers import registry
from studio.providers.llm import web_search_status
from studio.workers.pipeline.schemas import S, _obj

TASK = "video_ideas"
WEB_PROVIDER = "claude_code"
MIN_IDEAS, MAX_IDEAS = 10, 25
MAX_COMPETITORS, MAX_KEYWORDS, MAX_COMMENTS = 25, 25, 60
MAX_DONE, MAX_OPEN = 150, 50
NO_PROOF_PENALTY = 2.0
NO_PROOF_NOTE = "sem prova"
FILL_DAYS, FILL_STEP = 7, 3  # data sugerida para ideia sem data: hoje + 7, + 10, + 13...
UNAVAILABLE_MESSAGE = ("A síntese precisa do Claude Code com busca na web (WebSearch e WebFetch) como LLM em uso; "
                       "a rodada guardou só os sinais")
EVIDENCE_KINDS = ("proprio", "concorrente", "busca", "comentario", "web")
REF_KINDS = {"P": "proprio", "C": "concorrente", "K": "busca", "M": "comentario"}
WHEN_TYPES = ("evergreen", "data", "janela")
RISK_LEVELS = ("baixo", "medio", "alto")
FORMAT_NAMES = {"long": "vídeo longo", "short": "Short"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VIDEO_ID_RE = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/)([0-9A-Za-z_-]{11})")

SYSTEM = """Você é o estrategista de conteúdo do canal do YouTube descrito no pedido. Proponha de 10 a 25 vídeos com
mais chance de desempenho PARA ESTE CANAL, ranqueados, cada um com a data ou a janela de publicação.

Regras:
1. Toda ideia cita pelo menos uma evidência: o `ref` de um item dos SINAIS (ex.: C3, K7, M12, P2; copie o ref como
   veio, com a url vazia) ou, em evidência "web", uma URL que você abriu com WebFetch nesta sessão.
2. Nunca invente URL nem número. Views, inscritos e curtidas só aparecem citando o ref do sinal.
3. Use a web com moderação (no máximo umas 8 buscas): notícias, lançamentos e datas do nicho nos próximos 60 dias e
   tendências que os sinais não mostram.
4. Não repita os títulos de FEITOS nem das IDEIAS ABERTAS.
5. Use só os formatos do canal (lista FORMATOS). Longos e Shorts são públicos diferentes: compare um Short com os
   Shorts dos sinais e um longo com os longos. Equilibre os formatos conforme o que funciona no canal (sinais
   próprios) e o que os concorrentes mostram.
6. A nota de 0 a 10 é a chance relativa dentro do canal: 8+ só com evidência forte de 2 fontes diferentes.
7. "when": a data de publicação sugerida (AAAA-MM-DD, depois de hoje). type "data" = dia certo (lançamento,
   aniversário, evento); "janela" = de date até until (notícia quente, temporada); "evergreen" = qualquer dia (dê
   mesmo assim a data sugerida, espalhando no calendário). Em note, o porquê da data.
8. Risco: direitos/imagem, factual, saturação, fora da persona, desmonetização.
9. Mercado "us" nos sinais: ângulos que ainda não existem em português são oportunidade; diga isso no "why".
10. Em "lacunas", liste curto o que você não conseguiu medir ou verificar nesta rodada (ex.: busca sem resultado,
    tendência sem fonte). Escreva tudo em português do Brasil."""


def schema(formats: list[str]) -> dict[str, Any]:
    idea = _obj({
        "rank": {"type": "integer"},
        "title": S,
        "format": {"type": "string", "enum": list(formats)},
        "angle": S,
        "why": S,
        "evidence": {"type": "array", "items": _obj({
            "kind": {"type": "string", "enum": list(EVIDENCE_KINDS)}, "ref": S, "url": S, "note": S})},
        "when": _obj({"type": {"type": "string", "enum": list(WHEN_TYPES)}, "date": S, "until": S, "note": S}),
        "needs": {"type": "array", "items": S},
        "score": {"type": "number"},
        "risk": _obj({"level": {"type": "string", "enum": list(RISK_LEVELS)}, "note": S}),
    })
    return _obj({
        "resumo": S,
        "temas": {"type": "array", "items": S},
        "ideas": {"type": "array", "items": idea},  # 10 a 25 pelo SYSTEM (a saída estrita não aceita minItems > 1)
        "web_sources": {"type": "array", "items": _obj({"title": S, "url": S, "date": S})},
        "lacunas": {"type": "array", "items": S},
    })


# ----------------------------------------------------------------------------- contexto


def _ref(refs: dict[str, dict[str, Any]], prefix: str, item: dict[str, Any], title: str) -> str:
    ref = f"{prefix}{sum(1 for r in refs if r.startswith(prefix)) + 1}"
    refs[ref] = {"url": item.get("url") or "", "kind": REF_KINDS[prefix], "title": title,
                 "video_id": (item.get("youtube_id") or item.get("id")) if prefix != "M" else None,
                 "term": item.get("term") or "" if prefix == "K" else ""}
    return ref


def build_context(channel: dict[str, Any], signals: dict[str, Any], *, formats: list[str], markets: list[str],
                  done: list[dict[str, Any]], open_ideas: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """O pedido em JSON (sinais cortados e com `ref`) mais `refs` (ref → url, tipo, título), que fica fora do prompt."""
    refs: dict[str, dict[str, Any]] = {}
    own = signals.get("own") or {}
    own_out: dict[str, Any] = {}
    for fmt in formats:
        part = own.get(fmt) or {}
        rows = []
        for group in ("top", "bottom"):
            for r in part.get(group) or []:
                rows.append({"ref": _ref(refs, "P", r, r.get("title", "")), "grupo": "melhor" if group == "top"
                             else "pior", "title": r.get("title", ""), "views": r.get("views"), "vpd": r.get("vpd"),
                             "mult": r.get("mult"), "ctr": r.get("ctr"), "retencao": r.get("pct_watched")})
        own_out[fmt] = {"videos": part.get("n", 0), "mediana_views_dia": part.get("med", 0), "itens": rows}
    competitors: dict[str, list[dict[str, Any]]] = {f: [] for f in formats}
    for r in ((signals.get("competitors") or {}).get("items") or [])[:MAX_COMPETITORS]:
        if r.get("format") in competitors:
            competitors[r["format"]].append({
                "ref": _ref(refs, "C", r, r.get("title", "")), "title": r.get("title", ""),
                "canal": r.get("channel", ""), "views": r.get("views"), "mult": r.get("mult"),
                "idade_d": r.get("idade_d")})
    keywords: dict[str, list[dict[str, Any]]] = {f: [] for f in formats}
    for r in ((signals.get("keywords") or {}).get("items") or [])[:MAX_KEYWORDS]:
        if r.get("format") in keywords:
            keywords[r["format"]].append({
                "ref": _ref(refs, "K", r, r.get("title", "")), "title": r.get("title", ""),
                "canal": r.get("channel", ""), "views": r.get("views"), "inscritos": r.get("subs"),
                "ratio": r.get("ratio"), "termo": r.get("term"), "mercado": r.get("market"),
                "fora_da_curva": r.get("outlier")})
    comments = [
        {"ref": _ref(refs, "M", r, r.get("text", "")[:80]), "tipo": r.get("kind"), "video": r.get("video_title"),
         "texto": r.get("text"), "curtidas": r.get("likes")}
        for r in ((signals.get("comments") or {}).get("items") or [])[:MAX_COMMENTS]
    ]
    suggestions = [s.get("text") for s in own.get("suggestions") or [] if isinstance(s, dict) and s.get("text")]
    return {
        "today": today.isoformat(),
        "channel": channel,
        "formats": list(formats),
        "markets": list(markets),
        "own": own_out,
        "own_suggestions": suggestions,
        "competitors": competitors,
        "keywords": keywords,
        "comments": comments,
        "done": [d["title"] for d in done][:MAX_DONE],
        "open_ideas": [i["title"] for i in open_ideas][:MAX_OPEN],
        "refs": refs,
    }


def build_prompt(ctx: dict[str, Any]) -> str:
    ch = ctx["channel"]
    fmts = ", ".join(f"{f} ({FORMAT_NAMES[f]})" for f in ctx["formats"])
    blocks = {
        "SINAIS DO PRÓPRIO CANAL (views por dia; mult = vídeo ÷ mediana do formato)": ctx["own"],
        "SUGESTÕES DO DESEMPENHO": ctx["own_suggestions"],
        "CONCORRENTES FORA DA CURVA (mult = views ÷ mediana do próprio concorrente no formato)": ctx["competitors"],
        "BUSCAS POR PALAVRA-CHAVE (ratio = views ÷ inscritos)": ctx["keywords"],
        "COMENTÁRIOS DO PÚBLICO (perguntas e pedidos)": ctx["comments"],
        "FEITOS (não repetir)": ctx["done"],
        "IDEIAS ABERTAS (não repetir)": ctx["open_ideas"],
    }
    head = (
        f"Hoje: {ctx['today']}.\n"
        f"Canal: {ch.get('name', '')}. Nicho: {ch.get('niche') or '-'}. Idiomas: {', '.join(ch.get('languages') or [])}.\n"
        f"Persona: {ch.get('persona') or '-'}\nPúblico: {ch.get('audience') or '-'}\n"
        f"FORMATOS do canal: {fmts}. Mercados pesquisados: {', '.join(ctx['markets'])}.\n"
    )
    return head + "\n".join(f"\n{name}:\n{json.dumps(value, ensure_ascii=False)}" for name, value in blocks.items())


# ----------------------------------------------------------------------------- chamada


def web_ready() -> tuple[list[str] | None, str]:
    """A cadeia da síntese: no modo offline, o LLM de regras; senão só o Claude Code com a web (outro modelo
    inventaria links). Sem ele: (None, motivo)."""
    if get_settings().offline:
        return registry.resolve_chain("llm", None), ""
    ok, reason = web_search_status()
    return ([WEB_PROVIDER], "") if ok else (None, reason)


def synthesize(ctx: dict[str, Any], *, job_id: str | None = None) -> dict[str, Any]:
    """`{data, tokens, api_equiv_usd, provider}`, ou `{unavailable: True, message}` sem a web. Erro do provedor
    (limite do plano, tempo esgotado) sobe como `ProviderError`."""
    chain, reason = web_ready()
    if chain is None:
        return {"unavailable": True, "message": f"{UNAVAILABLE_MESSAGE} ({reason})."}
    prompt = build_prompt(ctx)
    sch = schema(ctx["formats"])
    llm_ctx = {k: v for k, v in ctx.items() if k != "refs"}

    def call(provider):
        return provider.complete_json(task=TASK, system=SYSTEM, prompt=prompt, schema=sch, max_tokens=16000,
                                      context=llm_ctx)

    result, provider, _ = registry.run_chain(
        "llm", chain, call, usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd), job_id=job_id,
    )
    details = result.usage.details or {}
    return {"data": result.data, "tokens": int(result.usage.units or 0),
            "api_equiv_usd": float(details.get("api_equivalent_usd") or 0.0), "provider": provider.info.id}


# ----------------------------------------------------------------------------- pós-processamento


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _video_id(url: str) -> str | None:
    m = VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else None


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _evidence(raw: list[Any], refs: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Troca o ref pela URL real do sinal; link do YouTube só vale se for de um sinal; "web" passa para a conferência
    HTTP. Devolve (evidências, descartadas)."""
    by_video = {r["video_id"]: ref for ref, r in refs.items() if r.get("video_id")}
    by_url = {r["url"]: ref for ref, r in refs.items() if r.get("url")}
    out: list[dict[str, Any]] = []
    dropped = 0
    for ev in raw or []:
        if not isinstance(ev, dict):
            dropped += 1
            continue
        ref = str(ev.get("ref") or "").strip().upper()
        url = str(ev.get("url") or "").strip()
        note = _text(ev.get("note"), 300)
        if ref not in refs:  # sem ref válido: o link de um sinal (mesma URL ou mesmo vídeo) ainda vale
            vid = _video_id(url)
            ref = by_url.get(url) or (by_video.get(vid, "") if vid else "")
        if ref:
            sig = refs[ref]
            item = {"kind": sig["kind"], "ref": ref, "url": sig["url"], "title": _text(sig["title"], 160),
                    "note": note, "checked": True, "term": sig.get("term") or ""}
        elif ev.get("kind") == "web" and url.startswith(("http://", "https://")) and not _video_id(url):
            item = {"kind": "web", "ref": "", "url": url[:2000], "title": "", "note": note, "checked": False,
                    "term": ""}
        else:
            dropped += 1
            continue
        if not any(e["url"] == item["url"] and e["ref"] == item["ref"] for e in out):
            out.append(item)
    return out, dropped


def postprocess(data: Any, refs: dict[str, dict[str, Any]], formats: list[str],
                today: date) -> dict[str, Any]:
    """Ideias limpas (refs traduzidos, formato do canal, nota de 0 a 10, data válida ou vazia) e as descartadas."""
    ideas: list[dict[str, Any]] = []
    discarded: list[dict[str, str]] = []
    raw = data.get("ideas") if isinstance(data, dict) else None
    for item in (raw if isinstance(raw, list) else [])[:MAX_IDEAS]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), 100)
        if not title:
            continue
        fmt = item.get("format")
        if fmt not in formats:
            discarded.append({"title": title, "motivo": f"formato fora do canal ({fmt or 'vazio'})"})
            continue
        evidence, dropped = _evidence(item.get("evidence") or [], refs)
        when = item.get("when") if isinstance(item.get("when"), dict) else {}
        start, until = _date(when.get("date")), _date(when.get("until"))
        wtype = when.get("type") if when.get("type") in WHEN_TYPES else "evergreen"
        risk = item.get("risk") if isinstance(item.get("risk"), dict) else {}
        try:
            score = max(0.0, min(10.0, float(item.get("score") or 0)))
        except (TypeError, ValueError):
            score = 0.0
        ideas.append({
            "title": title,
            "format": fmt,
            "angle": _text(item.get("angle"), 600),
            "why": _text(item.get("why"), 800),
            "evidence": evidence,
            "refs_dropped": dropped,
            "when": {"type": wtype, "date": start.isoformat() if start and start >= today else "",
                     "until": until.isoformat() if until and start and until >= start else "",
                     "note": _text(when.get("note"), 300)},
            "needs": [_text(n, 200) for n in item.get("needs") or [] if _text(n, 200)][:8],
            "score": round(score, 1),
            "risk": {"level": risk.get("level") if risk.get("level") in RISK_LEVELS else "medio",
                     "note": _text(risk.get("note"), 400)},
        })
    return {"ideas": ideas, "discarded": discarded}


def finalize(ideas: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Depois da conferência dos links: ideia sem nenhuma evidência perde 2 pontos e ganha "sem prova"; ideia sem
    data ganha uma data sugerida; ordem por nota (empate: a data mais cedo) e `rank` regravado."""
    filled = 0
    for idea in ideas:
        idea["no_proof"] = not idea["evidence"]
        if idea["no_proof"]:
            idea["score"] = round(max(0.0, idea["score"] - NO_PROOF_PENALTY), 1)
            note = idea["risk"]["note"]
            idea["risk"]["note"] = f"{NO_PROOF_NOTE}; {note}" if note else NO_PROOF_NOTE
        if not idea["when"]["date"]:
            idea["when"]["date"] = (today + timedelta(days=FILL_DAYS + FILL_STEP * filled)).isoformat()
            idea["when"]["note"] = (idea["when"]["note"] + " " if idea["when"]["note"] else "") + "(data sugerida "\
                "pelo Vulto: a síntese não deu data válida)"
            idea["when"]["until"] = ""
            filled += 1
    ideas.sort(key=lambda i: (-i["score"], i["when"]["date"]))
    for n, idea in enumerate(ideas, start=1):
        idea["rank"] = n
    return ideas


def agenda(ideas: list[dict[str, Any]], include: tuple[str, ...] = ("nova", "parecida", "ja_no_banco")) \
        -> list[dict[str, Any]]:
    """As ideias por data de publicação (a repetida fica de fora, a não ser que tenha sido salva à mão):
    `[{date, until, rank, title, format, score}]`."""
    rows = [i for i in ideas if (i.get("check") or {}).get("status", "nova") in include
            or (i.get("check") or {}).get("saved")]
    rows.sort(key=lambda i: (i["when"]["date"], -i["score"]))
    return [{"date": i["when"]["date"], "until": i["when"].get("until", ""), "type": i["when"]["type"],
             "rank": i["rank"], "title": i["title"], "format": i["format"], "score": i["score"]} for i in rows]
