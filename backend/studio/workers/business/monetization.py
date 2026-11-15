"""Monetização além do AdSense (bloco D, ADR 0002): frentes, ofertas de afiliado e links na descrição.

Origem: seção "dinheiro" do `estudio/estudio.py` do canais (`monetizacao`, `sugestao_id`, `link_oferta`,
`escolher_ofertas`, `montar_links`, `dinheiro_do_canal`) e o `[monetizacao]` do `plantao-vice/canal.toml`.

- A configuração do canal fica em `Channel.settings["monetization"]` (padrões em `DEFAULTS`); as ofertas, na tabela
  `affiliate_offers`.
- O "alvo" dos links é o id do projeto: `tracking_ids[<id do projeto>]` é o ID de rastreamento próprio do vídeo.
- O projeto liga os links em `Project.settings["affiliate"] = {"enabled", "mode", "offer_ids", "topics"}`; `offer_ids`
  guarda o id da oferta (`ofe_...`), que não muda quando o apelido (slug) é trocado; slugs antigos ainda valem.
- Os links entram no topo da descrição do YouTube e o aviso legal no fim (só o texto é cortado para caber).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from studio.db import session_scope
from studio.models import AffiliateOffer, Channel, Project

FRONT_STATUSES = ("ideia", "preparando", "ativa", "bloqueada", "pausada", "descartada")
OFFER_STATUSES = ("ideia", "ativa", "pausada", "descartada")
MODES = ("temas", "ofertas")  # como o projeto escolhe as ofertas
CHECK_DAYS = 3 # oferta conferida há mais tempo que isso pede nova conferência antes de publicar
DESCRIPTION_MAX = 5000

PUBLI = "#publi · links de afiliado: o canal ganha uma comissão e você não paga nada a mais."
AMAZON_DISCLOSURE = "Como participante do Programa de Associados da Amazon, sou remunerado pelas compras qualificadas efetuadas."

DEFAULTS: dict[str, Any] = {
    "plan": "",
    "since": "",  # 1º mês (AAAA-MM) com link de afiliado no ar
    "amazon_tag": "",  # ID de rastreamento padrão (o da loja), ex. "plantaovice-20"
    "amazon_domain": "www.amazon.com.br",
    "max_links": 3,
    "publi": PUBLI,
    "publi_pinned": "",  # texto do comentário fixado; vazio usa o `publi`
    "disclosure": AMAZON_DISCLOSURE,  # aviso legal da última linha (só quando há link da Amazon)
    "tracking_ids": {},  # {id do projeto (ou lugar): ID de rastreamento próprio}
    "fronts": [],  # [{id, name, status, next, requirement}]
}


class MonetizationError(ValueError):
    pass


# --------------------------------------------------------------------------------------------
# Configuração do canal
# --------------------------------------------------------------------------------------------


def config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Configuração com os padrões (canal sem nada configurado = padrões)."""
    cfg = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in DEFAULTS and v is not None}}
    cfg["tracking_ids"] = {str(k): str(v) for k, v in (cfg.get("tracking_ids") or {}).items() if str(v).strip()}
    cfg["fronts"] = [_front(f) for f in cfg.get("fronts") or []]
    try:
        cfg["max_links"] = max(1, min(int(cfg["max_links"]), 10))
    except (TypeError, ValueError):
        cfg["max_links"] = DEFAULTS["max_links"]
    return cfg


def _front(f: dict[str, Any]) -> dict[str, Any]:
    out = {k: str(f.get(k) or "") for k in ("id", "name", "status", "next", "requirement")}
    out["status"] = out["status"] or "ideia"
    return out


def channel_config(channel_id: str) -> dict[str, Any]:
    with session_scope() as s:
        ch = s.get(Channel, channel_id)
        if ch is None:
            raise LookupError(channel_id)
        return config((ch.settings or {}).get("monetization"))


def save_channel_config(channel_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Grava só as chaves enviadas (as outras ficam como estavam)."""
    for f in patch.get("fronts") or []:
        if (f.get("status") or "ideia") not in FRONT_STATUSES:
            raise MonetizationError(f"status de frente inválido: {f.get('status')} (use {', '.join(FRONT_STATUSES)})")
    if patch.get("since") and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", patch["since"]):
        raise MonetizationError("'desde' no formato AAAA-MM (ex.: 2026-11)")
    with session_scope() as s:
        ch = s.get(Channel, channel_id)
        if ch is None:
            raise LookupError(channel_id)
        settings = dict(ch.settings or {})
        current = dict(settings.get("monetization") or {})
        current.update({k: v for k, v in patch.items() if k in DEFAULTS})
        settings["monetization"] = config(current)
        ch.settings = settings
        return settings["monetization"]


# --------------------------------------------------------------------------------------------
# Ofertas
# --------------------------------------------------------------------------------------------


def offer_dict(row: AffiliateOffer, today: date | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "slug": row.slug,
        "name": row.name,
        "store": row.store or "amazon",
        "asin": row.asin or "",
        "link": row.link or "",
        "text": row.text or "",
        "topics": list(row.topics or []),
        "status": row.status,
        "checked_on": row.checked_on or "",
        "note": row.note or "",
        "stale": is_stale(row.checked_on or "", today),
    }


def is_stale(checked_on: str, today: date | None = None) -> bool:
    """Nunca conferida ou conferida há mais de `CHECK_DAYS` dias: pede nova conferência."""
    days = check_days(checked_on, today)
    return days is None or days > CHECK_DAYS


def check_days(checked_on: str, today: date | None = None) -> int | None:
    """Dias desde a última conferência (None quando nunca foi conferida ou a data não vale)."""
    if not checked_on:
        return None
    try:
        return ((today or date.today()) - date.fromisoformat(checked_on)).days
    except ValueError:
        return None


def channel_offers(channel_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.query(AffiliateOffer).filter_by(channel_id=channel_id).order_by(AffiliateOffer.created_at).all()
        return [offer_dict(r) for r in rows]


def suggest_tracking_id(cfg: dict[str, Any], target: str) -> str:
    """Nome sugerido para o ID de rastreamento de um vídeo (ex.: plantaovice-20 + prj_ab12 → plantaoviceprjab12-20)."""
    tag = cfg.get("amazon_tag", "")
    base, suffix = (tag.rsplit("-", 1) + [""])[:2] if "-" in tag else (tag, "20")
    return f"{base}{re.sub(r'[^a-z0-9]', '', target.lower())}-{suffix or '20'}"


def offer_link(cfg: dict[str, Any], offer: dict[str, Any], target: str | None) -> tuple[str, str]:
    """(link, ID de rastreamento). Com ID próprio do alvo e ASIN monta /dp/<ASIN>?tag=<id>; sem ele, usa o link curto
    (feito com o ID padrão) ou monta com o ID padrão."""
    if (offer.get("store") or "amazon") != "amazon":
        return offer.get("link", ""), ""
    default, own = cfg.get("amazon_tag", ""), (cfg.get("tracking_ids") or {}).get(target or "")
    if own and offer.get("asin"):
        return f"https://{cfg['amazon_domain']}/dp/{offer['asin']}?tag={own}", own
    if offer.get("link"):
        return offer["link"], default
    if offer.get("asin") and default:
        return f"https://{cfg['amazon_domain']}/dp/{offer['asin']}?tag={default}", default
    return "", ""


def choose_offers(offers: list[dict[str, Any]], ids: list[str], topics: list[str]) -> list[dict[str, Any]]:
    """Ofertas pedidas por id (slug ou id; têm de estar ativas) ou, sem ids, as ativas de tema "sempre" + as dos temas."""
    by_id = {o["slug"]: o for o in offers} | {o["id"]: o for o in offers if o.get("id")}
    if ids:
        missing = [i for i in ids if i not in by_id]
        if missing:
            names = ", ".join(o["slug"] for o in offers) or "(nenhuma)"
            raise MonetizationError(f"oferta {', '.join(missing)} não existe. Ofertas: {names}")
        stopped = [f"{by_id[i]['slug']} ({by_id[i].get('status')})" for i in ids if by_id[i].get("status") != "ativa"]
        if stopped:
            raise MonetizationError(f"oferta não ativa: {', '.join(stopped)}. Ative em Dinheiro depois de conferir.")
        return [by_id[i] for i in ids]
    wanted = {t.strip().lower() for t in topics if t.strip()}
    return [o for o in offers if o.get("status") == "ativa"
            and ("sempre" in o.get("topics", []) or wanted & {t.lower() for t in o.get("topics", [])})]


def build_links(cfg: dict[str, Any], target: str, offers: list[dict[str, Any]], is_short: bool = False,
                today: date | None = None, max_links: int | None = None) -> dict[str, Any]:
    """Topo da descrição, comentário fixado, frase do fim e checklist de publicação de um vídeo."""
    cfg = config(cfg)
    limit = max_links or int(cfg["max_links"])
    items = []
    for o in offers[:limit]:
        link, tracking = offer_link(cfg, o, target)
        if not link:
            raise MonetizationError(f"oferta {o['slug']} sem link nem ASIN (com ID de rastreamento padrão)")
        items.append({"id": o.get("id", ""), "slug": o["slug"], "name": o.get("name") or o["slug"],
                      "text": o.get("text") or o.get("name") or o["slug"], "link": link, "tracking_id": tracking,
                      "store": o.get("store") or "amazon", "checked_on": o.get("checked_on", ""), "note": o.get("note", "")})
    if not items:
        return {"target": target, "offers": [], "top": "", "pinned": "", "end": "",
                "checklist": ["Nenhuma oferta ativa para este vídeo."]}
    amazon = any(i["store"] == "amazon" for i in items)
    top = "\n".join(f"{i['text']}: {i['link']}" for i in items) + "\n" + cfg["publi"]
    pinned = f"{items[0]['text']}: {items[0]['link']}\n{cfg.get('publi_pinned') or cfg['publi']}"
    checklist = [
        'Marcar "Inclui promoção paga" (Detalhes › Mostrar mais › Promoção paga).',
        'Fala e tela: selo PUBLI nas cenas da oferta e "é link de afiliado" falado; preço da Amazon nunca em número no vídeo.',
    ]
    if amazon and not (cfg.get("tracking_ids") or {}).get(target):
        checklist.append(
            f"ID de rastreamento: os links usam o padrão ({cfg.get('amazon_tag') or '?'}). Para medir este vídeo sozinho, "
            f'crie "{suggest_tracking_id(cfg, target)}" em Associados › Gerenciar IDs de rastreamento e cadastre o nome '
            "em Dinheiro › IDs por vídeo para este projeto."
        )
    for i in items:
        days = check_days(i["checked_on"], today)
        when = f"conferida em {i['checked_on']}" if i["checked_on"] else "nunca conferida"
        alert = days is None or days > CHECK_DAYS
        checklist.append(f"{'CONFERIR' if alert else 'ok'}: {i['name']} ({when}) — vendedor, estoque e preço na página, "
                         "no navegador, pelo link limpo /dp/<ASIN> (sem a tag, para não clicar no próprio link).")
    if is_short:
        checklist.append("Short: link na descrição não é clicável; use o Vídeo relacionado apontando para o vídeo longo.")
    return {"target": target, "offers": items, "top": top, "pinned": pinned,
            "end": cfg.get("disclosure", "") if amazon else "", "checklist": checklist}


# --------------------------------------------------------------------------------------------
# Projeto
# --------------------------------------------------------------------------------------------


def _is_short(project_id: str) -> bool:
    from studio.core.project import service
    from studio.core.styles.library import get_style

    try:
        doc = service.get_doc(project_id)
        return get_style(doc.style_id, doc.style_overrides).format.type == "short"
    except Exception:  # noqa: BLE001 - estilo apagado ou inválido: trata como longo
        return False


def project_affiliate(project_id: str, today: date | None = None) -> dict[str, Any]:
    """Escolha do projeto e os links prontos (ofertas, topo, fixado, fim, checklist) com os erros, sem levantar."""
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise LookupError(project_id)
        aff = dict((row.settings or {}).get("affiliate") or {})
        channel_id = row.channel_id
        parent_id = row.parent_id
        parent_title = None
        if parent_id and (parent := s.get(Project, parent_id)) is not None:
            parent_title = parent.title
    is_short = _is_short(project_id)
    offer_ids = [str(x) for x in aff.get("offer_ids") or []]
    out: dict[str, Any] = {
        "enabled": bool(aff.get("enabled")),
        # "ofertas": só as marcadas (nenhuma marcada = nenhum link); "temas": "sempre" + as dos temas.
        "mode": aff.get("mode") if aff.get("mode") in MODES else ("ofertas" if offer_ids else "temas"),
        "offer_ids": offer_ids,
        "topics": [str(x) for x in aff.get("topics") or []],
        "channel_id": channel_id,
        "is_short": is_short,
        "parent_id": parent_id,
        "parent_title": parent_title,
        "target": project_id,
        "tracking_id": "",
        "suggested_tracking_id": "",
        "available": [],
        "offers": [],
        "top": "",
        "pinned": "",
        "end": "",
        "checklist": [],
        "errors": [],
    }
    if not channel_id:
        out["errors"].append("O projeto não tem canal: escolha o canal para usar as ofertas dele.")
        return out
    cfg = channel_config(channel_id)
    offers = channel_offers(channel_id)
    out["available"] = offers
    out["tracking_id"] = cfg["tracking_ids"].get(project_id, "")
    out["suggested_tracking_id"] = suggest_tracking_id(cfg, project_id) if cfg.get("amazon_tag") else ""
    try:
        if out["mode"] == "ofertas":
            chosen = choose_offers(offers, offer_ids, []) if offer_ids else []
        else:
            chosen = choose_offers(offers, [], out["topics"])
        out.update({k: v for k, v in build_links(cfg, project_id, chosen, is_short, today).items() if k != "target"})
    except MonetizationError as exc:
        out["errors"].append(str(exc))
    return out


def save_project_affiliate(project_id: str, patch: dict[str, Any]) -> None:
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise LookupError(project_id)
        settings = dict(row.settings or {})
        aff = dict(settings.get("affiliate") or {})
        for key in ("enabled", "mode", "offer_ids", "topics"):
            if key in patch and patch[key] is not None:
                aff[key] = patch[key]
        settings["affiliate"] = aff
        row.settings = settings
        channel_id = row.channel_id
    if "tracking_id" in patch and patch["tracking_id"] is not None and channel_id:
        cfg = channel_config(channel_id)
        ids = dict(cfg["tracking_ids"])
        if patch["tracking_id"].strip():
            ids[project_id] = patch["tracking_id"].strip()
        else:
            ids.pop(project_id, None)
        save_channel_config(channel_id, {"tracking_ids": ids})


def description_links(project_id: str | None, today: date | None = None) -> dict[str, Any]:
    """O que entra na descrição do vídeo: {"top", "end", "pinned", "checklist", "errors"}. Vazio quando o projeto
    não liga os links, não tem oferta ativa ou a escolha tem erro (o erro vai em `errors`)."""
    empty = {"top": "", "end": "", "pinned": "", "checklist": [], "errors": []}
    if not project_id:
        return empty
    try:
        aff = project_affiliate(project_id, today)
    except LookupError:
        return empty
    if not aff["enabled"]:
        return empty
    if aff["errors"]:
        return {**empty, "errors": aff["errors"]}
    if not aff["offers"]:
        return {**empty, "errors": ["links ligados, mas nenhuma oferta ativa combina com este vídeo"]}
    return {k: aff[k] for k in ("top", "end", "pinned", "checklist", "errors")}


def fit_description(body: str, tail: str = "", *, top: str = "", end: str = "", limit: int = DESCRIPTION_MAX) -> str:
    """Descrição com topo (links), texto, cauda (créditos e hashtags) e fim (aviso legal). Só o texto é cortado para
    caber no limite; topo, cauda e fim ficam inteiros. Se nem assim couber, corta a cauda (créditos e hashtags) e
    nunca o aviso legal do fim."""
    top, tail, end = top.strip(), tail.strip(), end.strip()
    after = "\n\n".join(p for p in (tail, end) if p)
    fixed = [p for p in (top, after) if p]
    room = limit - sum(len(p) for p in fixed) - 2 * len(fixed)
    if room < 0 and tail:
        # Topo + cauda + aviso já passam do limite: a cauda fica com o espaço que sobra depois do topo e do aviso.
        keep = limit - sum(len(p) + 2 for p in (top, end) if p)
        tail = tail[: max(0, keep)].rstrip()
        after = "\n\n".join(p for p in (tail, end) if p)
        room = 0
    cut = body.strip()[: max(0, room)].rstrip()
    text = "\n\n".join(p for p in (top, cut, after) if p)
    if len(text) > limit and end:  # só topo + aviso já não cabem: o aviso fica, o topo é cortado
        head = "\n\n".join(p for p in (top, cut) if p)[: max(0, limit - len(end) - 2)].rstrip()
        return f"{head}\n\n{end}" if head else end[:limit]
    return text[:limit]


# --------------------------------------------------------------------------------------------
# Resumo do canal (página Dinheiro)
# --------------------------------------------------------------------------------------------


def channel_money(channel_id: str, today: date | None = None) -> dict[str, Any]:
    cfg = channel_config(channel_id)
    offers = []
    for o in channel_offers(channel_id):
        link, tracking = offer_link(cfg, o, None)
        offers.append({**o, "url": link, "tracking_id": tracking, "stale": is_stale(o["checked_on"], today)})
    return {"plan": cfg["plan"], "since": cfg["since"], "fronts": cfg["fronts"], "offers": offers,
            "tracking_ids": cfg["tracking_ids"]}
