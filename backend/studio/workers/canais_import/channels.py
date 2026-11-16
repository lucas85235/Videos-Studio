"""Canal do canais → `Channel` do Vulto: associa (ou cria) e preenche os campos que vinham do canal.toml.

Regra para não estragar o que já foi editado no Vulto: um campo só é preenchido quando está vazio (ou no padrão).
O nome de um canal associado nunca muda. A ligação fica em `Channel.settings["canais"]["id"]`, e é ela que faz a
segunda importação achar o mesmo canal (não cria outro).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from studio.ids import new_id
from studio.models import AffiliateOffer, Channel
from studio.workers.canais_import.reader import CanaisChannel

AUTO, SKIP = "auto", "pular"

# Formato do canais → formato do Vulto (ideias, estoque e escopo das lições).
FORMATS = {"doc-longo": "long", "longo": "long", "long": "long",
           "corte-short": "short", "short-noticia": "short", "short": "short"}

_ELEVEN_ID = re.compile(r"\(([A-Za-z0-9]{16,})\)")


def normalize(name: str) -> str:
    """Nome para comparar: sem acento, sem caixa e sem espaços duplicados."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().split())


def youtube_id_of(ch: CanaisChannel) -> str:
    return str((ch.toml.get("youtube") or {}).get("channel_id") or ch.db.get("channel_id") or "").strip()


def linked_slug(row: Channel) -> str:
    """Canal do canais a que `row` foi ligado numa importação anterior ("" = nenhum)."""
    return str(((row.settings or {}).get("canais") or {}).get("id") or "")


def stock_format_of(toml: dict[str, Any]) -> str | None:
    """Formato do estoque: `[agenda] estoque_formato` ou, sem ele, deduzido de `formatos`. Canal de longo (com ou sem
    cortes) conta episódios; só Shorts conta Shorts; os dois tipos de vídeo próprios contam tudo."""
    agenda = toml.get("agenda") or {}
    fmt = FORMATS.get(str(agenda.get("estoque_formato") or ""))
    if fmt:
        return fmt
    formats = {str(x) for x in toml.get("formatos") or []}
    if not formats:
        return None
    own = {FORMATS.get(x) for x in formats if x != "corte-short"} - {None}  # o corte vem do longo, não é estoque
    if own == {"long"}:
        return "long"
    if own == {"short"} or (not own and "corte-short" in formats):
        return "short"
    return "any" if own == {"long", "short"} else None


def resolve(s: Session, ch: CanaisChannel, choice: str) -> tuple[str, Channel | None, str]:
    """(ação, canal do Vulto, motivo). Ação: "associar", "criar" ou "pular".

    `choice`: "auto" (procura pelo vínculo de antes, pelo canal do YouTube e pelo nome; senão cria), "pular" ou o id
    de um canal do Vulto (associa a ele).
    """
    choice = (choice or AUTO).strip()
    if choice == SKIP:
        return "pular", None, "escolhido para ficar de fora"
    if choice != AUTO:
        row = s.get(Channel, choice)
        if row is None:
            raise ValueError(f"canal do Vulto não encontrado: {choice}")
        return "associar", row, "escolhido à mão"
    rows = s.query(Channel).order_by(Channel.created_at).all()
    for row in rows:
        if linked_slug(row) == ch.slug:
            return "associar", row, "importado antes"
    # Canal já ligado a OUTRO canal do canais não serve: as linhas dos dois se misturariam e a ligação se perderia.
    rows = [row for row in rows if not linked_slug(row)]
    yt = youtube_id_of(ch)
    if yt:
        for row in rows:
            if str(((row.settings or {}).get("youtube") or {}).get("channel_id") or "") == yt:
                return "associar", row, "mesmo canal do YouTube"
    for row in rows:
        if normalize(row.name) == normalize(ch.name):
            return "associar", row, "mesmo nome"
    row = Channel(id=new_id("chn"), name=ch.name[:200], settings={})
    s.add(row)
    s.flush()
    return "criar", row, "não existe no Vulto"


def _voice(text: str) -> dict[str, Any] | None:
    """`[voz] final` do canal.toml (texto livre). Só a ElevenLabs com o id da voz entre parênteses é reconhecida."""
    if "eleven" not in (text or "").casefold():
        return None
    match = _ELEVEN_ID.search(text)
    return {"final": {"provider": "elevenlabs", "voice": match.group(1)}} if match else None


def _posting(toml: dict[str, Any]) -> dict[str, Any] | None:
    from studio.workers.publish.schedule import PostingPlan, Slot

    times = (toml.get("agenda") or {}).get("horarios") or []
    if not times:
        return None
    try:
        plan = PostingPlan(timezone=str(toml.get("fuso") or "America/Sao_Paulo"),
                           slots=[Slot(time=str(t), reason="importado do canais") for t in times])
    except ValidationError:
        return None
    return plan.model_dump()


def _monetization(toml: dict[str, Any]) -> dict[str, Any] | None:
    from studio.workers.business.monetization import config

    m = toml.get("monetizacao") or {}
    if not m:
        return None
    raw = {
        "plan": m.get("plano"),
        "since": m.get("desde"),
        "amazon_tag": m.get("amazon_tag") or (toml.get("afiliado") or {}).get("tag"),
        "max_links": m.get("max_links"),
        "publi": m.get("publi"),
        "publi_pinned": m.get("publi_fixado"),
        "disclosure": m.get("aviso"),
        "tracking_ids": m.get("amazon_ids") or {},
        "fronts": [
            {"id": f.get("id"), "name": f.get("nome"), "status": f.get("status"), "next": f.get("proximo"),
             "requirement": f.get("requisito")}
            for f in m.get("frente") or []
        ],
    }
    return config(raw)


def fill(s: Session, row: Channel, ch: CanaisChannel, created: bool) -> list[str]:
    """Preenche os campos vazios de `row` com o canal.toml (e a linha da tabela `canais`). Devolve os rótulos do que
    mudou."""
    t, db = ch.toml, ch.db
    agenda, motor, voz = t.get("agenda") or {}, t.get("motor") or {}, t.get("voz") or {}
    changed: list[str] = []

    def put(attr: str, value: Any, label: str, empty: Any = "") -> None:
        if value in (None, "") or getattr(row, attr) not in (empty, None):
            return
        setattr(row, attr, value)
        changed.append(label)

    put("niche", str(t.get("nicho") or db.get("nicho") or "")[:200], "nicho")
    put("audience", str(t.get("publico") or ""), "público")
    put("cadence", str(agenda.get("cadencia") or ""), "cadência")
    if isinstance(agenda.get("posts_por_dia"), int):
        put("posts_per_day", max(0, min(int(agenda["posts_por_dia"]), 20)), "posts por dia", empty=0)
    if isinstance(agenda.get("estoque_minimo"), int):
        put("min_stock", max(0, min(int(agenda["estoque_minimo"]), 50)), "estoque mínimo", empty=0)
    fmt = stock_format_of(t)
    if fmt and (created or row.stock_format in ("", None)):
        row.stock_format = fmt
        changed.append("formato do estoque")
    shorts = motor.get("shorts") if isinstance(motor.get("shorts"), dict) else {}
    tag = str(shorts.get("hashtag") or "")
    if re.fullmatch(r"#\S{1,59}", tag):
        put("short_hashtag", tag, "hashtag dos Shorts")
    if isinstance(voz.get("creditos_por_caractere"), int | float):
        put("credits_per_char", float(voz["creditos_por_caractere"]), "créditos por caractere", empty=0.0)
    voice = _voice(str(voz.get("final") or ""))
    if voice and not row.voice_defaults:
        row.voice_defaults = voice
        changed.append("voz final padrão")
    lang = str(t.get("idioma") or db.get("idioma") or "").strip()
    if created and lang:
        row.languages = [lang]
        changed.append("idioma")

    settings = dict(row.settings or {})
    posting = _posting(t)
    if posting and not ((settings.get("posting") or {}).get("slots")):
        settings["posting"] = posting
        changed.append("horários")
    money = _monetization(t)
    if money and not settings.get("monetization"):
        settings["monetization"] = money
        changed.append("monetização")
    yt = t.get("youtube") or {}
    link = {
        "id": ch.slug,
        "youtube_channel_id": youtube_id_of(ch),
        "handle": str(yt.get("handle") or db.get("handle") or ""),
        "toml": ch.toml_path,
    }
    if settings.get("canais") != link:
        settings["canais"] = link
    row.settings = settings
    return changed


def offers(s: Session, row: Channel, ch: CanaisChannel) -> tuple[int, int]:
    """Ofertas `[[monetizacao.oferta]]` → `AffiliateOffer` (chave: canal + slug). Devolve (novas, já existiam)."""
    new = existing = 0
    for o in (ch.toml.get("monetizacao") or {}).get("oferta") or []:
        slug = str(o.get("id") or "").strip()[:60]
        if not slug:
            continue
        found = s.query(AffiliateOffer).filter_by(channel_id=row.id, slug=slug).first()
        if found is not None:
            existing += 1
            continue
        status = str(o.get("status") or "ideia")
        s.add(AffiliateOffer(
            id=new_id("ofe"), channel_id=row.id, slug=slug, name=str(o.get("nome") or slug)[:200],
            store=str(o.get("loja") or "amazon")[:40], asin=str(o.get("asin") or "")[:20], link=str(o.get("link") or ""),
            text=str(o.get("texto") or ""), topics=[str(x) for x in o.get("temas") or []],
            status=status if status in ("ideia", "ativa", "pausada", "descartada") else "ideia",
            checked_on=str(o.get("conferido") or "")[:10], note=str(o.get("nota") or ""),
        ))
        new += 1
    s.flush()
    return new, existing
