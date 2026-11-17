"""Rodada da pesquisa de vídeos de um canal: coleta os sinais, pede a síntese, confere e grava as ideias.

Etapas (cada uma isolada: se falhar, vira uma falha e uma lacuna da rodada, e a rodada continua):
1. próprio canal (sem cota); 2. concorrentes (YouTube Data API, sem `search`); 3. buscas por palavra-chave em cada
mercado (balde próprio de 100 buscas por dia, contado no medidor da cota); 4. comentários (banco local); 5. síntese
(Claude Code com WebSearch/WebFetch); 6. checagem contra os vídeos feitos e o banco de ideias, conferência dos links e
gravação (`origin="pesquisa"`).
Sem a web (ou com o limite do plano), a rodada fica "parcial": os sinais ficam guardados e nenhuma ideia é criada.
Cancelada ou com erro depois da coleta, a rodada também fica no histórico ("cancelada" ou "falhou"), com os sinais.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Idea, Job, ResearchRun
from studio.providers.base import ProviderError
from studio.workers.business import ideas as idea_bank
from studio.workers.pipeline.build import BuildCancelled
from studio.workers.publish import quota
from studio.workers.radar import access as radar_access
from studio.workers.radar.analysis import MERCADOS, fmt
from studio.workers.research import check, config, signals, synthesis

log = logging.getLogger(__name__)

KIND = "video_research"
ORIGIN = "pesquisa"
STEPS = ("own", "competitors", "keywords", "comments", "synthesis")
STEP_NAMES = {"own": "próprio canal", "competitors": "concorrentes", "keywords": "buscas", "comments": "comentários",
              "synthesis": "síntese"}
MANUAL_COOLDOWN_H = 6  # uma rodada manual a cada 6 h por canal (o limite de 5 h do plano Claude)
SAVE_STATUSES = ("nova", "parecida")
ANALYTICS_GAP = ("sem fontes de tráfego, termos de busca do YouTube nem curva de retenção (o Vulto ainda não lê "
                 "essas dimensões do Analytics)")
WHEN_LABELS = {"evergreen": "qualquer dia", "data": "data certa", "janela": "janela"}
# Uma pesquisa por vez: a checagem "já há uma rodada?" e o envio do job andam juntos (rota e rotina da manhã).
SUBMIT_LOCK = threading.Lock()
_IDEAS_LOCK = threading.Lock()  # "Salvar ideia" lê e grava a rodada: dois cliques não se atropelam
KIND_LABELS = {"proprio": "Meu canal", "concorrente": "Concorrente", "busca": "Busca", "comentario": "Comentário",
               "web": "Web"}


class ResearchRunError(RuntimeError):
    """A rodada não pode começar (vira 422 na API)."""


class RunNotFound(LookupError):
    pass


# ----------------------------------------------------------------------------- consultas


def active_job(channel_id: str) -> str | None:
    """Id do job `video_research` do canal na fila ou rodando, se houver."""
    with session_scope() as s:
        rows = (s.query(Job).filter(Job.kind == KIND, Job.status.in_(("queued", "running")))
                .order_by(Job.created_at.desc()).all())
        return next((r.id for r in rows if (r.payload or {}).get("channel_id") == channel_id), None)


def any_active_job() -> str | None:
    """Id de qualquer pesquisa na fila ou rodando (de qualquer canal): a rotina não junta duas sínteses no plano."""
    with session_scope() as s:
        row = (s.query(Job).filter(Job.kind == KIND, Job.status.in_(("queued", "running")))
               .order_by(Job.created_at.desc()).first())
        return row.id if row else None


def channel_info(channel_id: str) -> dict[str, Any]:
    with session_scope() as s:
        ch = s.get(Channel, channel_id)
        if ch is None:
            raise config.ChannelNotFound(f"canal não encontrado: {channel_id}")
        return {"id": ch.id, "name": ch.name, "niche": ch.niche or "", "persona": (ch.persona or "")[:1500],
                "audience": (ch.audience or "")[:800], "languages": list(ch.languages or [])}


def hours_since_last(channel_id: str, now: datetime | None = None) -> float | None:
    with session_scope() as s:
        row = (s.query(ResearchRun).filter(ResearchRun.channel_id == channel_id)
               .order_by(ResearchRun.created_at.desc()).first())
        if row is None or row.created_at is None:
            return None
        return ((now or datetime.now(UTC)) - row.created_at).total_seconds() / 3600


def next_manual_at(channel_id: str, now: datetime | None = None) -> str | None:
    """Quando a próxima rodada manual fica liberada (ISO, UTC), ou None se já pode."""
    with session_scope() as s:
        row = (s.query(ResearchRun).filter(ResearchRun.channel_id == channel_id)
               .order_by(ResearchRun.created_at.desc()).first())
        last = row.created_at if row is not None else None
    if last is None:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    free = last + timedelta(hours=MANUAL_COOLDOWN_H)
    return free.isoformat().replace("+00:00", "Z") if free > (now or datetime.now(UTC)) else None


def readiness(channel_id: str) -> dict[str, Any]:
    """O que a rodada tem para trabalhar: acesso à API, web e cota prevista (para a tela e para o 422)."""
    cfg = config.get_config(channel_id)
    chain, reason = synthesis.web_ready()
    access = radar_access.access_state()
    return {"access": access, "web": {"ok": chain is not None, "message": reason},
            "plan": signals.plan_units(cfg, search_left=search_left_today()),
            "formats": config.channel_formats(channel_id, cfg)}


def search_left_today() -> int | None:
    """Buscas que cabem hoje, pelo medidor, quando o acesso é o token de um canal vinculado (o mesmo projeto)."""
    try:
        acc = radar_access.resolve_access()
    except radar_access.RadarAccessError:
        return None
    if acc.account is None:
        return None
    try:
        return quota.search_left()
    except Exception:  # noqa: BLE001 - sem o medidor, vale o orçamento
        return None


# ----------------------------------------------------------------------------- rodada


def _gap_lines(cfg: dict[str, Any], sig: dict[str, Any], plan: dict[str, Any], skip: list[str],
               has_access: bool) -> list[str]:
    out: list[str] = []
    for step in skip:
        out.append(f"etapa pulada nesta rodada: {STEP_NAMES.get(step, step)}")
    for f in sig.get("falhas") or []:
        out.append(f"{STEP_NAMES.get(f['etapa'], f['etapa'])}: {f['erro']}")
    own = sig.get("own") or {}
    if "own" not in skip and own and not any((own.get(k) or {}).get("n") for k in ("long", "short")):
        out.append("sem métricas dos vídeos do próprio canal (vincule o YouTube com o login do Google para a coleta "
                   "diária de métricas)")
    elif own and not any(r.get("ctr") is not None for k in ("long", "short")
                         for r in (own.get(k) or {}).get("top") or []):
        out.append("sem CTR de miniatura nos vídeos do próprio canal (relatório de alcance ainda não chegou)")
    if not has_access and ("competitors" not in skip or "keywords" not in skip):
        out.append("sem acesso à YouTube Data API: concorrentes e buscas não rodaram (cole uma chave em Radar)")
    if "competitors" not in skip and not cfg.get("competitors"):
        out.append("sem concorrentes cadastrados (aba Configuração)")
    if "keywords" not in skip and not cfg.get("keywords"):
        out.append("sem palavras-chave para buscar (aba Configuração ou palavras-chave do canal no YouTube)")
    if plan.get("pulados") and "keywords" not in skip:
        motivo = ("do limite de buscas do dia" if plan.get("pulados_motivo") == "buscas do dia"
                  else f"do orçamento de {plan['budget']} unidades")
        out.append(f"buscas fora {motivo}: {', '.join(plan['pulados'])}")
    if "comments" not in skip and not (sig.get("comments") or {}).get("items"):
        out.append(f"nenhuma pergunta ou pedido nos comentários dos últimos {cfg['days']} dias (a caixa de "
                   "comentários precisa ter sido lida)")
    out.append(ANALYTICS_GAP)
    return out


def _collect(step: str, sig: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    try:
        res = fn()
    except Exception as exc:  # noqa: BLE001 - uma etapa não derruba a rodada
        log.warning("pesquisa: etapa %s falhou: %s", step, exc)
        sig.setdefault("falhas", []).append({"etapa": step, "erro": str(exc)[:300] or type(exc).__name__})
        return None
    if res.get("erro"):
        sig.setdefault("falhas", []).append({"etapa": step, "erro": str(res["erro"])[:300]})
    return res


def _save_run(run_id: str, channel_id: str, *, job_id: str | None, now: datetime, status: str, params: dict[str, Any],
              api: radar_access.Api | None, sig: dict[str, Any], synth: dict[str, Any], gaps: list[str],
              idea_ids: list[str], tokens: int = 0, usd: float = 0.0, error: str | None = None) -> None:
    params = {**params, "search_calls": api.buscas if api else 0}
    with session_scope() as s:
        s.add(ResearchRun(
            id=run_id, channel_id=channel_id, job_id=job_id, created_at=now, finished_at=datetime.now(UTC),
            status=status, error=error, params=params, units=api.unidades if api else 0, llm_tokens=tokens,
            llm_api_equiv_usd=usd, signals=sig, synthesis=synth, gaps=list(dict.fromkeys(gaps)), idea_ids=idea_ids,
        ))
    config.update_state(channel_id, last_run_id=run_id)


def run(channel_id: str, *, job_id: str | None = None, skip: list[str] | tuple[str, ...] = (),
        weekly: str | None = None, http: httpx.Client | None = None, web_http: httpx.Client | None = None,
        now: datetime | None = None, progress: Callable[[float, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Uma rodada completa. `http`: cliente da YouTube Data API (testes); `web_http`: cliente da conferência dos
    links de notícia. Devolve o resumo `{run_id, status, ideas, saved, units, searches, lacunas, retry}`.

    Situações: "ok" (a síntese rodou, mesmo que nenhuma ideia tenha sobrado), "parcial" (a síntese não rodou: sem a
    web, etapa pulada ou erro do Claude; `retry` diz se foi um erro passageiro, como o limite do plano), "cancelada"
    (o pedido de cancelar chegou antes de gravar as ideias: os sinais e a síntese já paga ficam guardados, as ideias
    não vão para o banco e a rodada levanta `BuildCancelled`) e "falhou" (erro depois da coleta: a rodada fica no
    histórico com o erro e os sinais, e o erro sobe para o job)."""
    say = progress or (lambda _f, _m: None)
    stop = cancelled or (lambda: False)
    now = now or datetime.now(UTC)
    today = now.astimezone().date()
    skip = [s for s in skip if s in STEPS]
    cfg = config.get_config(channel_id)
    channel = channel_info(channel_id)
    formats = config.channel_formats(channel_id, cfg)
    run_id = new_id("rsr")
    sig: dict[str, Any] = {"falhas": []}

    api = None
    try:
        acc = radar_access.resolve_access()
        # a pesquisa conta as buscas no balde próprio delas (100 por dia), não na cota geral
        api = radar_access.Api(acc, int(cfg.get("budget") or 600), http=http, custo_busca=signals.SEARCH_UNITS)
    except radar_access.RadarAccessError:
        acc = None
    quota_gap = ""
    search_left: int | None = None
    if api is not None and api.medir:  # mesmo projeto do Google Cloud dos envios: respeita o medidor
        try:
            info = quota.status(now)
            search_left = quota.search_left(now)
        except Exception as exc:  # noqa: BLE001 - sem o medidor, a rodada segue com o orçamento
            log.debug("medidor da cota não lido: %s", exc)
        else:
            if info["general_over"]:
                quota_gap = "a cota geral do YouTube de hoje acabou: concorrentes e buscas não rodaram"
                skip = [*skip, *(s for s in ("competitors", "keywords") if s not in skip)]
            elif search_left == 0 and "keywords" not in skip:
                quota_gap = (f"as {quota.SEARCH_LIMIT} buscas do dia no YouTube já foram usadas (pesquisa e radar): "
                             "as buscas não rodaram")
    plan = signals.plan_units(cfg, skip, search_left=search_left)

    say(0.05, "Lendo o desempenho do canal")
    if "own" not in skip:
        own = _collect("own", sig, lambda: signals.own_signals(channel_id))
        if own is not None:  # se falhou, a etapa fica de fora (a tela diz que o canal não foi lido)
            sig["own"] = own
    say(0.15, "Concorrentes")
    if api is not None and "competitors" not in skip and cfg["competitors"]:
        res = _collect("competitors", sig, lambda: signals.competitor_signals(
            api, cfg["competitors"], days=cfg["days"], agora=now))
        if res is not None:
            sig["competitors"] = {"items": res["items"], "channels": res.get("channels") or []}
    say(0.30, "Buscas")
    if api is not None and "keywords" not in skip and plan["searches"]:
        res = _collect("keywords", sig, lambda: signals.keyword_signals(
            api, plan["searches"], days=cfg["days"], agora=now))
        if res is not None:
            sig["keywords"] = {"items": res["items"], "searches": res.get("searches") or [],
                               "pulados": plan["pulados"]}
    say(0.45, "Comentários")
    if "comments" not in skip:
        res = _collect("comments", sig, lambda: signals.comment_signals(channel_id, days=cfg["days"], now=now))
        if res is not None:
            sig["comments"] = {"items": res["items"]}

    gaps = _gap_lines(cfg, sig, plan, skip, acc is not None)
    if quota_gap:
        gaps.insert(0, quota_gap)
    synth: dict[str, Any] = {"resumo": "", "temas": [], "ideas": [], "web_sources": [], "discarded": [],
                             "agenda": []}
    params = {"markets": cfg["markets"], "days": cfg["days"], "budget": cfg["budget"],
              "competitors": [c.get("name") or c["id"] for c in cfg["competitors"]], "keywords": cfg["keywords"],
              "keywords_us": cfg["keywords_us"], "formats": formats, "skip": skip, "weekly": weekly}
    spent: dict[str, Any] = {"tokens": 0, "usd": 0.0}
    save = {"run_id": run_id, "channel_id": channel_id, "job_id": job_id, "now": now, "params": params, "api": api,
            "sig": sig, "synth": synth, "gaps": gaps}
    # Daqui em diante a cota já foi gasta: qualquer saída (cancelamento ou erro) grava a rodada com os sinais.
    try:
        status, idea_ids, retry = _synthesize_and_save(
            run_id, channel_id, channel, cfg, sig, synth, gaps, skip, formats, today, spent, say, stop, web_http,
            job_id)
    except BuildCancelled:
        gaps.insert(0, 'pesquisa cancelada: as ideias não foram para o banco (use "Salvar ideia" na rodada)')
        _record_quietly(save, "cancelada", error="cancelada por você", spent=spent)
        raise
    except Exception as exc:
        _record_quietly(save, "falhou", error=(str(exc) or type(exc).__name__)[:500], spent=spent)
        raise
    _save_run(run_id, channel_id, job_id=job_id, now=now, status=status, params=params, api=api, sig=sig,
              synth=synth, gaps=gaps, idea_ids=idea_ids, tokens=spent["tokens"], usd=spent["usd"])
    return {"run_id": run_id, "status": status, "ideas": len(synth["ideas"]), "saved": len(idea_ids),
            "units": api.unidades if api else 0, "searches": api.buscas if api else 0,
            "lacunas": len(set(gaps)), "retry": retry}


def _record_quietly(save: dict[str, Any], status: str, *, error: str, spent: dict[str, Any]) -> None:
    """Grava a rodada que parou (cancelada ou com erro) sem esconder o erro original."""
    try:
        _save_run(save["run_id"], save["channel_id"], job_id=save["job_id"], now=save["now"], status=status,
                  params=save["params"], api=save["api"], sig=save["sig"], synth=save["synth"], gaps=save["gaps"],
                  idea_ids=[], tokens=spent["tokens"], usd=spent["usd"], error=error)
    except Exception as exc:  # noqa: BLE001 - o erro que importa é o da rodada
        log.warning("pesquisa: rodada %s não gravada: %s", save["run_id"], exc)


def _synthesize_and_save(run_id: str, channel_id: str, channel: dict[str, Any], cfg: dict[str, Any],
                         sig: dict[str, Any], synth: dict[str, Any], gaps: list[str], skip: list[str],
                         formats: list[str], today: Any, spent: dict[str, Any], say: Callable[[float, str], None],
                         stop: Callable[[], bool], web_http: httpx.Client | None,
                         job_id: str | None) -> tuple[str, list[str], bool]:
    """Síntese, checagem e gravação das ideias. Devolve `(status, idea_ids, retry)`; preenche `synth`, `gaps` e
    `spent`. O cancelamento é conferido antes do Claude e logo antes de gravar no banco (a síntese já paga fica)."""
    done = check.done_titles(channel_id)
    bank = check.open_ideas(channel_id)
    ctx = synthesis.build_context(channel, sig, formats=formats, markets=cfg["markets"], done=done,
                                  open_ideas=[i for i in bank if i["status"] in check.OPEN_STATUSES], today=today)
    if stop():
        raise BuildCancelled("pesquisa cancelada")  # antes da chamada ao Claude
    ran, retry = False, False
    if "synthesis" not in skip:
        say(0.50, "Síntese com pesquisa na web (pode levar até 30 min)")
        try:
            out = synthesis.synthesize(ctx, job_id=job_id)
        except ProviderError as exc:  # limite do plano, tempo esgotado, cadeia sem provedor: a rotina tenta de novo
            out = {"unavailable": True, "message": f"a síntese falhou: {str(exc)[:300]}"}
            retry = True
        if out.get("unavailable"):
            gaps.insert(0, out["message"])
        else:
            ran = True
            spent["tokens"], spent["usd"] = out["tokens"], out["api_equiv_usd"]
            data = out["data"] if isinstance(out["data"], dict) else {}
            say(0.90, "Conferindo ideias e links")
            post = synthesis.postprocess(data, ctx["refs"], formats, today)
            removed = [] if stop() else check.validate_urls(post["ideas"], http=web_http)
            if removed:
                gaps.append(f"{len(removed)} link(s) de notícia não abriram e saíram das evidências")
            ideas = synthesis.finalize(post["ideas"], today)
            dropped = sum(i.pop("refs_dropped", 0) for i in ideas)
            if dropped:
                gaps.append(f"{dropped} evidência(s) da síntese sem sinal correspondente foram descartadas")
            gaps += [synthesis._text(g, 300) for g in data.get("lacunas") or [] if synthesis._text(g, 300)]
            synth.update({
                "resumo": synthesis._text(data.get("resumo"), 2000),
                "temas": [synthesis._text(t, 120) for t in data.get("temas") or [] if synthesis._text(t, 120)][:12],
                "web_sources": [
                    {"title": synthesis._text(w.get("title"), 200), "url": str(w.get("url") or "")[:2000],
                     "date": synthesis._text(w.get("date"), 20)}
                    for w in data.get("web_sources") or [] if isinstance(w, dict) and w.get("url")][:30],
                "discarded": post["discarded"],
                "ideas": ideas,
            })
            if not ideas:
                gaps.insert(0, "a síntese rodou, mas nenhuma ideia sobrou (veja as descartadas)" if post["discarded"]
                            else "a síntese rodou, mas não devolveu ideias")
    cancel = stop()  # último ponto: depois daqui as ideias vão para o banco e a rodada fecha
    idea_ids = _check_and_save(run_id, channel_id, synth["ideas"], done, bank, save=not cancel)
    synth["agenda"] = synthesis.agenda(synth["ideas"])
    if cancel:
        raise BuildCancelled("pesquisa cancelada")
    return ("ok" if ran else "parcial"), idea_ids, retry


# ----------------------------------------------------------------------------- ideias


def _day(iso: str) -> str:
    """ "AAAA-MM-DD" → "DD/MM/AAAA" (o resto passa como veio)."""
    parts = iso.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 and all(p.isdigit() for p in parts) else iso


def evidence_text(idea: dict[str, Any], run_id: str, run_day: str = "") -> str:
    """O texto da evidência no banco de ideias, em português de tela (sem id interno). Termina com a origem:
    "Pesquisa de vídeos de DD/MM/AAAA, ideia #N"."""
    evs = []
    for ev in idea["evidence"]:
        label = KIND_LABELS.get(ev["kind"], ev["kind"])
        title = f' "{ev["title"]}"' if ev.get("title") else ""
        evs.append(f"{label}{title} {ev['url']}".strip())
    w = idea["when"]
    when = _day(w["date"]) + (f" a {_day(w['until'])}" if w.get("until") else "") + (
        f" ({WHEN_LABELS.get(w['type'], w['type'])})")
    lines = [
        f"Por que: {idea['why']}" if idea.get("why") else "",
        f"Evidências: {'; '.join(evs)}" if evs else "Evidências: nenhuma (sem prova)",
        f"Quando: {when}" + (f" — {w['note']}" if w.get("note") else ""),
        f"Precisa: {', '.join(idea['needs'])}" if idea.get("needs") else "",
        f"Risco: {idea['risk']['level']}" + (f" — {idea['risk']['note']}" if idea["risk"].get("note") else ""),
        f"Pesquisa de vídeos{' de ' + _day(run_day) if run_day else ''}, ideia #{idea['rank']}",
    ]
    return "\n".join(x for x in lines if x)


def _idea_data(idea: dict[str, Any], channel_id: str, run_id: str, run_day: str = "") -> dict[str, Any]:
    search = next((ev["term"] for ev in idea["evidence"] if ev.get("term")), "")
    return {"channel_id": channel_id, "title": idea["title"], "angle": idea.get("angle") or "",
            "format": idea["format"], "origin": ORIGIN, "evidence": evidence_text(idea, run_id, run_day),
            "search": search, "score": idea["score"]}


def _save_one(idea: dict[str, Any], channel_id: str, run_id: str, force: bool = False,
              run_day: str = "") -> str | None:
    try:
        row = idea_bank.create(_idea_data(idea, channel_id, run_id, run_day), force=force)
    except idea_bank.DuplicateIdea as exc:
        idea["check"] = {**idea["check"], "status": "ja_no_banco", "idea_id": exc.idea["id"],
                         "similar_to": {"title": exc.idea["title"], "url": ""}}
        return None
    idea["check"]["idea_id"] = row["id"]
    idea["check"]["saved"] = True
    return row["id"]


def _check_and_save(run_id: str, channel_id: str, ideas: list[dict[str, Any]], done: list[dict[str, Any]],
                    bank: list[dict[str, Any]], *, save: bool = True) -> list[str]:
    """Confere cada ideia (contra os vídeos feitos, o banco e as ideias já gravadas nesta rodada) e grava as novas e
    as parecidas. Sem `save` (rodada cancelada), só confere."""
    saved: list[str] = []
    bank = list(bank)
    run_day = datetime.now(UTC).astimezone().date().isoformat()
    for idea in ideas:
        idea["check"] = check.check_idea(idea, done, bank)
        if idea["check"]["status"] == "parecida":
            sim = idea["check"]["similar_to"] or {}
            note = f'parecida com "{sim.get("title", "")}", já feito'
            idea["risk"]["note"] = f"{note}; {idea['risk']['note']}" if idea["risk"]["note"] else note
        if save and idea["check"]["status"] in SAVE_STATUSES and (
                new := _save_one(idea, channel_id, run_id, run_day=run_day)):
            saved.append(new)
            bank.append({"id": new, "title": idea["title"], "status": "nova"})  # a próxima da rodada não repete
    return saved


def to_ideas(run_id: str, ranks: list[int], force: bool = False) -> dict[str, Any]:
    """Grava no banco as ideias da rodada que ficaram de fora (repetida, parecida ou sem gravar). Com `force`, grava
    mesmo com título igual a uma ideia que já existe. Lê e grava a rodada sob trava: dois pedidos ao mesmo tempo não
    apagam a ideia criada pelo outro. A ideia salva à mão entra na agenda (`check.saved`)."""
    with _IDEAS_LOCK:
        with session_scope() as s:
            row = s.get(ResearchRun, run_id)
            if row is None:
                raise RunNotFound(f"rodada não encontrada: {run_id}")
            channel_id, synth, idea_ids = row.channel_id, dict(row.synthesis or {}), list(row.idea_ids or [])
            run_day = row.created_at.astimezone().date().isoformat() if row.created_at else ""
        ideas = list(synth.get("ideas") or [])
        created: list[str] = []
        skipped: list[dict[str, Any]] = []
        for idea in ideas:
            if idea.get("rank") not in ranks:
                continue
            chk = idea.setdefault("check", {"status": "nova", "similar_to": None, "idea_id": None})
            if chk.get("saved") and chk.get("idea_id"):
                skipped.append({"rank": idea["rank"], "motivo": "já está no banco", "idea_id": chk["idea_id"]})
                continue
            new = _save_one(idea, channel_id, run_id, force=force, run_day=run_day)
            if new:
                created.append(new)
            else:
                skipped.append({"rank": idea["rank"], "motivo": "já existe ideia com esse título",
                                "idea_id": idea["check"].get("idea_id")})
        synth["ideas"] = ideas
        synth["agenda"] = synthesis.agenda(ideas)
        with session_scope() as s:
            row = s.get(ResearchRun, run_id)
            row.synthesis = synth
            row.idea_ids = list(dict.fromkeys(idea_ids + created))
    return {"created": created, "skipped": skipped, "run": get_run(run_id)}


# ----------------------------------------------------------------------------- leitura


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def run_summary(row: ResearchRun) -> dict[str, Any]:
    ideas = (row.synthesis or {}).get("ideas") or []
    return {"id": row.id, "channel_id": row.channel_id, "job_id": row.job_id, "created_at": _iso(row.created_at),
            "finished_at": _iso(row.finished_at), "status": row.status, "error": row.error, "units": row.units or 0,
            "n_ideas": len(ideas), "n_saved": len(row.idea_ids or []), "lacunas": list(row.gaps or []),
            "params": row.params or {}}


def get_run(run_id: str) -> dict[str, Any]:
    """A rodada inteira; cada ideia gravada traz o status atual dela no banco (`check.idea_status`)."""
    with session_scope() as s:
        row = s.get(ResearchRun, run_id)
        if row is None:
            raise RunNotFound(f"rodada não encontrada: {run_id}")
        out = {**run_summary(row), "llm_tokens": row.llm_tokens or 0,
               "llm_api_equiv_usd": row.llm_api_equiv_usd or 0.0, "signals": row.signals or {},
               "synthesis": row.synthesis or {}, "idea_ids": list(row.idea_ids or [])}
        ids = [i["check"]["idea_id"] for i in out["synthesis"].get("ideas") or []
               if (i.get("check") or {}).get("idea_id")]
        statuses = {r.id: r.status for r in s.query(Idea).filter(Idea.id.in_(ids)).all()} if ids else {}
    for idea in out["synthesis"].get("ideas") or []:
        chk = idea.get("check") or {}
        chk["idea_status"] = statuses.get(chk.get("idea_id") or "")
    return out


def list_runs(channel_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = (s.query(ResearchRun).filter(ResearchRun.channel_id == channel_id)
                .order_by(ResearchRun.created_at.desc()).limit(limit).all())
        return [run_summary(r) for r in rows]


def last_run(channel_id: str) -> dict[str, Any] | None:
    runs = list_runs(channel_id, 1)
    return runs[0] if runs else None


def new_ideas_waiting(channel_id: str) -> int:
    """Ideias da pesquisa (de qualquer rodada) do canal ainda "nova" no banco, para Hoje: uma rodada nova sem ideias
    não esconde as da anterior que ainda esperam decisão."""
    with session_scope() as s:
        return s.query(Idea).filter(Idea.channel_id == channel_id, Idea.origin == ORIGIN,
                                    Idea.status == "nova").count()


def channel_new_ideas(channel_id: str) -> int:
    """Ideias "nova" do canal no banco (de qualquer origem), para o limite da rotina semanal."""
    with session_scope() as s:
        return s.query(Idea).filter(Idea.channel_id == channel_id, Idea.status == "nova").count()


# ----------------------------------------------------------------------------- relatório


def _num(v: Any) -> str:
    return fmt(v) if isinstance(v, int | float) else "-"


def _check_label(idea: dict[str, Any]) -> str:
    chk = idea.get("check") or {}
    st = chk.get("status", "nova")
    sim = (chk.get("similar_to") or {}).get("title")
    return {"nova": "nova", "parecida": f'parecida com "{sim}"', "repetida": f'repetida ("{sim}")',
            "ja_no_banco": "já estava no banco"}.get(st, st)


def report_markdown(run_id: str) -> str:
    """O relatório da rodada em Markdown: lacunas, resumo, agenda por data, ranking por nota e os sinais principais."""
    run = get_run(run_id)
    synth, sig = run["synthesis"], run["signals"]
    with session_scope() as s:
        ch = s.get(Channel, run["channel_id"])
        name = ch.name if ch else run["channel_id"]
    p = run["params"]
    markets = ", ".join(MERCADOS[m]["nome"] for m in p.get("markets") or [] if m in MERCADOS)
    out = [f"# Pesquisa de vídeos: {name}", "",
           f"Rodada {run['id']} de {(run['created_at'] or '')[:16].replace('T', ' ')} UTC · situação: {run['status']}"
           f" · cota: {run['units']} unidades · mercados: {markets or '-'} · janela: {p.get('days', '-')} dias"
           f" · formatos: {', '.join(p.get('formats') or [])}", ""]
    if run.get("error"):
        out += [f"Erro: {run['error']}", ""]
    if run["lacunas"]:
        out += ["## Lacunas (o que não deu para medir)", ""] + [f"- {g}" for g in run["lacunas"]] + [""]
    if synth.get("resumo"):
        out += ["## Resumo", "", synth["resumo"], ""]
    if synth.get("temas"):
        out += ["## Temas", ""] + [f"- {t}" for t in synth["temas"]] + [""]
    if synth.get("agenda"):
        out += ["## Agenda (por data de publicação)", "", "| Data | Formato | Nota | Ideia |", "|---|---|---|---|"]
        for a in synth["agenda"]:
            when = a["date"] + (f" a {a['until']}" if a.get("until") else "")
            out.append(f"| {when} | {a['format']} | {a['score']:.1f} | #{a['rank']} {a['title']} |")
        out.append("")
    ideas = synth.get("ideas") or []
    if ideas:
        out += ["## Ranking (por nota)", ""]
        for i in ideas:
            out.append(f"### {i['rank']}. {i['title']} ({i['format']}, nota {i['score']:.1f}) — {_check_label(i)}")
            out.append("")
            if i.get("angle"):
                out.append(f"- Ângulo: {i['angle']}")
            if i.get("why"):
                out.append(f"- Por que: {i['why']}")
            w = i["when"]
            out.append(f"- Quando: {w['date']}" + (f" a {w['until']}" if w.get("until") else "")
                       + f" ({WHEN_LABELS.get(w['type'], w['type'])})" + (f" — {w['note']}" if w.get("note") else ""))
            for ev in i["evidence"]:
                label = KIND_LABELS.get(ev["kind"], ev["kind"])
                out.append(f"- Evidência ({label}{' ' + ev['ref'] if ev.get('ref') else ''}): "
                           f"{ev.get('title') or ''} {ev['url']}".rstrip() + (f" — {ev['note']}" if ev.get("note")
                                                                               else ""))
            if i.get("needs"):
                out.append(f"- Precisa: {', '.join(i['needs'])}")
            out.append(f"- Risco: {i['risk']['level']}" + (f" — {i['risk']['note']}" if i["risk"].get("note") else ""))
            out.append("")
    if synth.get("discarded"):
        out += ["## Descartadas", ""] + [f"- {d['title']}: {d['motivo']}" for d in synth["discarded"]] + [""]
    for key, title in (("competitors", "Concorrentes fora da curva"), ("keywords", "Buscas")):
        items = (sig.get(key) or {}).get("items") or []
        for fmt_ in ("long", "short"):
            rows = [r for r in items if r.get("format") == fmt_][:10]
            if not rows:
                continue
            out += [f"## {title} ({'longos' if fmt_ == 'long' else 'Shorts'})", ""]
            for r in rows:
                extra = f"{r['mult']:.1f}× a mediana" if key == "competitors" else (
                    f"{r['ratio']:.1f}× inscritos · {r['term']} ({MERCADOS[r['market']]['nome']})")
                out.append(f"- {r['title']} — {r['channel']} · {_num(r['views'])} views · {extra} · {r['url']}")
            out.append("")
    comments = (sig.get("comments") or {}).get("items") or []
    if comments:
        out += ["## Público (perguntas e pedidos)", ""]
        out += [f"- ({c['kind']}, {c['likes']} curtidas) {c['text'][:200]} — {c['url']}" for c in comments[:15]]
        out.append("")
    if synth.get("web_sources"):
        out += ["## Fontes da web", ""] + [f"- {w['title']} ({w['date']}) {w['url']}" for w in synth["web_sources"]]
    return "\n".join(out).rstrip() + "\n"

