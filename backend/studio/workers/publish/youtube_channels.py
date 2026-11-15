"""Canal do Vulto ligado a um canal do YouTube que já existe.

Dois caminhos:
- login do Google (OAuth): traz nome, @, descrição, foto, país/idioma, palavras-chave, inscritos/vídeos e os títulos
  dos últimos vídeos, e guarda o token do canal para publicar nele;
- link colado (público, sem login): id do canal pelo link (ou pela página do canal, uma vez) e nome e títulos
  recentes pelo feed RSS oficial. Esse caminho não publica.

Os dados ficam em `Channel.settings["youtube"]` (sem migração do banco); a foto vai para
`<pasta de dados>/channels/<id do canal>/avatar.jpg`. "Preencher com IA" lê esses dados e propõe nicho, persona,
idiomas, estilo padrão e identidade visual; nada é salvo sem o usuário revisar.
"""

from __future__ import annotations

import io
import logging
import shlex
import shutil
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from studio.config import get_settings
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel
from studio.workers.publish import youtube as yt

log = logging.getLogger(__name__)

# Idiomas que a tela de canais oferece (os mesmos de LANGUAGES em apps/web/src/api.ts).
APP_LANGUAGES = ("pt-BR", "en-US", "es-ES", "es-MX", "fr-FR", "de-DE", "it-IT", "ja-JP", "ko-KR", "hi-IN", "ar-SA",
                 "ru-RU", "id-ID", "tr-TR")
_BY_PREFIX = {"pt": "pt-BR", "en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT", "ja": "ja-JP",
              "ko": "ko-KR", "hi": "hi-IN", "ar": "ar-SA", "ru": "ru-RU", "id": "id-ID", "in": "id-ID", "tr": "tr-TR"}
_BY_COUNTRY = {"BR": "pt-BR", "PT": "pt-BR", "AO": "pt-BR", "MZ": "pt-BR", "US": "en-US", "GB": "en-US", "CA": "en-US",
               "AU": "en-US", "IE": "en-US", "NZ": "en-US", "ES": "es-ES", "MX": "es-MX", "AR": "es-MX", "CO": "es-MX",
               "CL": "es-MX", "PE": "es-MX", "VE": "es-MX", "EC": "es-MX", "UY": "es-MX", "FR": "fr-FR", "DE": "de-DE",
               "AT": "de-DE", "IT": "it-IT", "JP": "ja-JP", "KR": "ko-KR", "RU": "ru-RU", "ID": "id-ID", "TR": "tr-TR",
               "SA": "ar-SA", "EG": "ar-SA", "AE": "ar-SA", "MA": "ar-SA"}

_AVATAR_HOSTS = (".ggpht.com", ".googleusercontent.com", ".ytimg.com")
AVATAR_MAX_BYTES = 5 * 1024 * 1024

# Campos de `settings["youtube"]` que a tela recebe.
_PUBLIC_KEYS = ("channel_id", "title", "handle", "description", "url", "avatar", "country", "language", "keywords",
                "subscribers", "videos", "views", "recent_videos", "source", "imported_at", "token_error")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def app_language(code: str | None, country: str | None = None) -> str | None:
    """Idioma do app a partir do idioma padrão do YouTube ("pt", "es-419"…) ou, sem ele, do país do canal."""
    raw = (code or "").strip().replace("_", "-")
    if raw:
        for lang in APP_LANGUAGES:
            if lang.lower() == raw.lower():
                return lang
        base, _, region = raw.partition("-")
        base = base.lower()
        if base == "es" and region and region.upper() != "ES":
            return "es-MX"  # es-419, es-US, es-AR…: espanhol latino
        if base in _BY_PREFIX:
            return _BY_PREFIX[base]
    return _BY_COUNTRY.get((country or "").strip().upper())


def parse_keywords(text: str) -> list[str]:
    """Palavras-chave do canal: separadas por espaço, com aspas nas de várias palavras."""
    try:
        words = shlex.split(text or "")
    except ValueError:
        words = (text or "").replace('"', " ").split()
    out: list[str] = []
    for word in words:
        word = word.strip().strip(",")[:60]
        if word and word.lower() not in {w.lower() for w in out}:
            out.append(word)
    return out[:30]


def parse_channel(item: dict[str, Any]) -> dict[str, Any]:
    """Dados do canal (channels.list com snippet, brandingSettings, statistics e contentDetails)."""
    snippet = item.get("snippet") or {}
    branding = (item.get("brandingSettings") or {}).get("channel") or {}
    stats = item.get("statistics") or {}
    uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads") or ""
    thumbs = snippet.get("thumbnails") or {}
    avatar = next((thumbs[k]["url"] for k in ("high", "medium", "default") if (thumbs.get(k) or {}).get("url")), "")
    youtube_id = item.get("id") or ""
    handle = (snippet.get("customUrl") or "").strip()
    handle = handle if handle.startswith("@") else ""
    country = (snippet.get("country") or branding.get("country") or "").strip().upper()
    return {
        "channel_id": youtube_id,
        "title": (snippet.get("title") or branding.get("title") or "").strip(),
        "handle": handle,
        "description": (snippet.get("description") or branding.get("description") or "").strip()[:5000],
        "url": f"{yt.SITE}/{handle}" if handle else f"{yt.SITE}/channel/{youtube_id}",
        "avatar_url": avatar,
        "country": country,
        "language": app_language(snippet.get("defaultLanguage") or branding.get("defaultLanguage"), country),
        "keywords": parse_keywords(branding.get("keywords") or ""),
        "subscribers": None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
        "videos": _int(stats.get("videoCount")),
        "views": _int(stats.get("viewCount")),
        "uploads_playlist": uploads,
    }


def youtube_data(settings: dict[str, Any] | None) -> dict[str, Any]:
    return dict((settings or {}).get("youtube") or {})


def token_status(data: dict[str, Any]) -> str:
    """ok (token do canal), global (a conta da aba de publicação é deste canal), error (o token falhou),
    public (ligado só pelo link) ou missing (desvinculado)."""
    youtube_id = data.get("channel_id")
    if yt.has_token(youtube_id):
        return "error" if data.get("token_error") else "ok"
    glob = yt.account()
    if glob["connected"] and glob["channel_id"] == youtube_id:
        return "global"
    return "public" if data.get("source") == "public" else "missing"


def public_info(settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """O que a tela de canais mostra do canal do YouTube (com a situação do token)."""
    data = youtube_data(settings)
    if not data.get("channel_id"):
        return None
    return {**{k: data.get(k) for k in _PUBLIC_KEYS}, "token": token_status(data)}


def avatar_rel(channel_id: str) -> str:
    return f"channels/{channel_id}/avatar.jpg"


def save_avatar(http: httpx.Client, url: str, channel_id: str) -> str | None:
    """Baixa a foto do canal (só dos servidores de imagem do Google) para a pasta de dados."""
    host = (urlparse(url).hostname or "").lower() if url else ""
    if not url.startswith("https://") or not host.endswith(_AVATAR_HOSTS):
        return None
    try:
        resp = http.get(url, headers=yt.PUBLIC_HEADERS, follow_redirects=True)
        if resp.status_code != 200 or len(resp.content) > AVATAR_MAX_BYTES:
            return None
        image = Image.open(io.BytesIO(resp.content))
        image.load()
        image = image.convert("RGB")
        image.thumbnail((256, 256))
        rel = avatar_rel(channel_id)
        path = get_settings().data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=90)
        return rel
    except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError) as exc:
        log.warning("foto do canal não baixada (%s)", exc)
        return None


def _owner(s, youtube_id: str) -> Channel | None:
    """Canal do Vulto já ligado a este canal do YouTube."""
    for row in s.query(Channel).all():
        if youtube_data(row.settings).get("channel_id") == youtube_id:
            return row
    return None


def _used_by_other(youtube_id: str, channel_id: str) -> bool:
    with session_scope() as s:
        owner = _owner(s, youtube_id)
        return owner is not None and owner.id != channel_id


def _pick_channel(youtube_id: str, title: str, target: str | None) -> str:
    """Canal do Vulto que recebe os dados: o alvo, o que já é deste canal do YouTube ou um novo."""
    with session_scope() as s:
        owner = _owner(s, youtube_id)
        if not target or target == yt.NEW_CHANNEL:
            return owner.id if owner else new_id("chn")
        if s.get(Channel, target) is None:
            raise KeyError(target)
        if owner is not None and owner.id != target:
            raise yt.YouTubeError(f"o canal “{title}” do YouTube já está ligado ao canal “{owner.name}” do Vulto")
        return target


def _store(channel_id: str, data: dict[str, Any], *, source: str, http: httpx.Client) -> bool:
    """Grava os dados do YouTube no canal do Vulto (cria o canal se ele não existir). Devolve se criou."""
    data = dict(data)
    avatar = save_avatar(http, data.pop("avatar_url", "") or "", channel_id)
    old_youtube_id = None
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        created = row is None
        previous: dict[str, Any] = {}
        if row is None:
            row = Channel(id=channel_id, name=(data.get("title") or "Canal do YouTube")[:200], niche="", persona="",
                          languages=[data.get("language") or "pt-BR"], default_style_id=None, style_bible={}, settings={})
            s.add(row)
        else:
            previous = youtube_data(row.settings)
            old_youtube_id = previous.get("channel_id")
        same = previous.get("channel_id") == data["channel_id"]
        if same and source == "public":
            # O link público traz menos campos: mantém o que um login anterior já tinha trazido.
            data = {**previous, **{k: v for k, v in data.items() if v not in (None, "", [])}}
            source = previous.get("source") or source
        data.update(source=source, imported_at=_now(), token_error=None,
                    avatar=avatar or (previous.get("avatar") if same else None))
        if data.get("title"):
            row.name = data["title"][:200]
        row.settings = {**(row.settings or {}), "youtube": data}
    if old_youtube_id and old_youtube_id != data["channel_id"] and not _used_by_other(old_youtube_id, channel_id):
        yt.forget_channel_token(old_youtube_id)
    return created


def link_from_oauth(http: httpx.Client, access_token: str, refresh_token: str, target: str) -> dict[str, Any]:
    """Fim do login de vínculo: lê o canal escolhido, guarda o token dele e cria/atualiza o canal do Vulto."""
    data = parse_channel(yt.fetch_channel(http, access_token))
    youtube_id = data["channel_id"]
    warnings: list[str] = []
    try:
        data["recent_videos"] = yt.fetch_recent_uploads(http, access_token, data["uploads_playlist"])
    except yt.YouTubeError as exc:
        data["recent_videos"] = []
        warnings.append(f"os vídeos recentes não vieram: {exc}")
    try:
        channel_id = _pick_channel(youtube_id, data["title"], target)
    except KeyError as exc:
        raise yt.YouTubeError("o canal do Vulto não existe mais; vincule de novo pela tela de Canais") from exc
    yt.save_channel_token(youtube_id, refresh_token, data["title"])
    created = _store(channel_id, data, source="oauth", http=http)
    return {"channel_title": data["title"], "channel_id": youtube_id, "vulto_channel_id": channel_id,
            "created": created, "warnings": warnings}


def _public_data(http: httpx.Client, text: str) -> dict[str, Any]:
    kind, value = yt.parse_channel_link(text)
    page: dict[str, str] = {}
    if kind == "id":
        youtube_id = value
    else:
        page = yt.read_channel_page(http, yt.page_path(kind, value))
        youtube_id = page["channel_id"]
    try:
        feed = yt.read_feed(http, youtube_id)
    except yt.YouTubeError:
        if not page.get("title"):
            raise
        feed = {"title": "", "videos": []}  # canal sem vídeos públicos: fica o que a página mostrou
    title = feed["title"] or page.get("title") or ""
    if not title:
        raise yt.YouTubeError("não consegui ler o nome do canal; confira o link")
    handle = value if kind == "handle" else ""
    return {
        "channel_id": youtube_id,
        "title": title,
        "handle": handle,
        "description": (page.get("description") or "")[:5000],
        "url": f"{yt.SITE}/{handle}" if handle else f"{yt.SITE}/channel/{youtube_id}",
        "avatar_url": page.get("avatar_url") or "",
        "country": "",
        "language": None,
        "keywords": [],
        "subscribers": None,
        "videos": None,
        "views": None,
        "uploads_playlist": "",
        "recent_videos": feed["videos"],
    }


def link_from_public(text: str, target: str | None = None, http: httpx.Client | None = None) -> tuple[str, bool]:
    """Liga (ou cria) um canal do Vulto a partir do link público do canal. Devolve (id do canal, criou?)."""
    own = http is None
    client = http or yt.new_client(timeout=30)
    try:
        data = _public_data(client, text)
        channel_id = _pick_channel(data["channel_id"], data["title"], target)
        return channel_id, _store(channel_id, data, source="public", http=client)
    finally:
        if own:
            client.close()


def refresh(channel_id: str, http: httpx.Client | None = None) -> list[str]:
    """Lê de novo os dados do canal no YouTube (com o token, se houver; senão pelo link público)."""
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        data = youtube_data(row.settings)
    youtube_id = data.get("channel_id")
    if not youtube_id:
        raise yt.YouTubeError("este canal não está ligado ao YouTube")
    own = http is None
    client = http or yt.new_client()
    warnings: list[str] = []
    try:
        if not yt.has_token(youtube_id):
            link_from_public(data.get("url") or youtube_id, channel_id, http=client)
            return warnings
        try:
            access = yt.access_token(client, yt.channel_key(youtube_id))
        except yt.YouTubeError as exc:
            _set_token_error(channel_id, str(exc))
            raise
        fresh = parse_channel(yt.fetch_channel(client, access, youtube_id))
        try:
            fresh["recent_videos"] = yt.fetch_recent_uploads(client, access, fresh["uploads_playlist"])
        except yt.YouTubeError as exc:
            fresh["recent_videos"] = data.get("recent_videos") or []
            warnings.append(f"os vídeos recentes não vieram: {exc}")
        _store(channel_id, fresh, source="oauth", http=client)
        return warnings
    finally:
        if own:
            client.close()


def _set_token_error(channel_id: str, message: str) -> None:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is not None:
            data = youtube_data(row.settings)
            row.settings = {**(row.settings or {}), "youtube": {**data, "token_error": message[:300]}}


def _remove_avatar(channel_id: str) -> None:
    folder = get_settings().data_dir / "channels" / channel_id
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def unlink(channel_id: str, *, forget: bool = False) -> None:
    """Desvincula: apaga só o token deste canal. `forget` também apaga os dados do YouTube guardados no canal."""
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        data = youtube_data(row.settings)
        settings = dict(row.settings or {})
        if forget:
            settings.pop("youtube", None)
        elif data:
            settings["youtube"] = {**data, "token_error": None}
        row.settings = settings
    youtube_id = data.get("channel_id")
    if youtube_id and not _used_by_other(youtube_id, channel_id):
        yt.forget_channel_token(youtube_id)
    if forget:
        _remove_avatar(channel_id)


def channel_deleted(channel_id: str, settings: dict[str, Any] | None) -> None:
    """Limpeza depois de apagar um canal do Vulto: o token do canal do YouTube (se ninguém mais usa) e a foto."""
    youtube_id = youtube_data(settings).get("channel_id")
    if youtube_id and not _used_by_other(youtube_id, channel_id):
        yt.forget_channel_token(youtube_id)
    _remove_avatar(channel_id)


# --------------------------------------------------------------------------------------------
# Preencher nicho, persona e identidade com IA
# --------------------------------------------------------------------------------------------

PROFILE_SYSTEM = """Você ajuda a configurar canais sem rosto ("dark") do YouTube no Vulto, um estúdio de vídeos
narrados. A partir dos dados reais de um canal (nome, descrição, palavras-chave e títulos recentes), proponha:
- niche: o nicho em poucas palavras (ex.: "curiosidades de ciência", "história antiga", "finanças pessoais");
- persona: como o narrador fala, em 2 a 4 frases (tom, ritmo, vocabulário, o que evita); ela orienta os roteiros;
- languages: os códigos de idioma da narração, o principal primeiro, só da lista dada;
- default_style_id: o id do estilo da lista que mais combina com o canal, ou null se nenhum combinar;
- identity: a identidade visual do canal em 1 a 3 frases (paleta, clima, tipo de imagem), sem citar marcas;
- reason: uma frase curta explicando o estilo escolhido.
Baseie-se só nos dados; não invente fatos sobre o canal. Escreva em português do Brasil."""


def _profile_schema() -> dict[str, Any]:
    from studio.workers.pipeline.schemas import NS, S, _obj

    return _obj({"niche": S, "persona": S, "languages": {"type": "array", "items": S}, "default_style_id": NS,
                 "identity": S, "reason": S})


def _llm(task: str, system: str, prompt: str, schema: dict[str, Any], context: dict[str, Any]):
    """Mesma cadeia de LLM das etapas de autoria (Claude da conta; o mock só no modo offline)."""
    from studio.providers import registry

    chain = registry.resolve_chain("llm", None)
    result, provider, _ = registry.run_chain(
        "llm",
        chain,
        lambda p: p.complete_json(task=task, system=system, prompt=prompt, schema=schema, context=context),
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
    )
    return result, provider.info.id


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def suggest_profile(channel_id: str) -> dict[str, Any]:
    """Proposta de nicho, persona, idiomas, estilo padrão e identidade visual (não grava nada)."""
    from studio.core.styles.library import list_styles

    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        name, niche, persona = row.name, row.niche or "", row.persona or ""
        languages = list(row.languages or ["pt-BR"])
        data = youtube_data(row.settings)
    if not data.get("channel_id"):
        raise ValueError("ligue o canal ao YouTube antes (Vincular ou Colar o link)")
    styles = {item["style"]["id"]: item["style"] for item in list_styles()}
    titles = [v.get("title", "") for v in data.get("recent_videos") or [] if v.get("title")]
    style_lines = [
        f"- {sid}: {st.get('name', sid)} ({(st.get('format') or {}).get('type', '')})"
        + (f" — {str(st.get('description') or '')[:160]}" if st.get("description") else "")
        for sid, st in styles.items()
    ]
    prompt = "\n".join([
        f"Canal: {data.get('title') or name}" + (f" ({data['handle']})" if data.get("handle") else ""),
        f"País: {data.get('country') or '(não informado)'}; idioma padrão: {data.get('language') or '(não informado)'}",
        f"Descrição: {data.get('description') or '(sem descrição)'}",
        f"Palavras-chave: {', '.join(data.get('keywords') or []) or '(nenhuma)'}",
        "Títulos recentes:",
        *([f"- {t}" for t in titles] or ["(nenhum vídeo público)"]),
        "",
        f"Nicho atual no app: {niche or '(vazio)'}",
        f"Persona atual no app: {persona or '(vazia)'}",
        f"Idiomas atuais no app: {', '.join(languages)}",
        "",
        f"Idiomas aceitos: {', '.join(APP_LANGUAGES)}",
        "Estilos disponíveis (id: nome):",
        *style_lines,
    ])
    context = {"title": data.get("title") or name, "titles": titles, "languages": languages,
               "style_ids": list(styles), "description": data.get("description") or ""}
    result, provider_id = _llm("channel_profile", PROFILE_SYSTEM, prompt, _profile_schema(), context)
    out = result.data or {}
    langs: list[str] = []
    for code in out.get("languages") or []:
        lang = app_language(str(code))
        if lang and lang not in langs:
            langs.append(lang)
    style_id = out.get("default_style_id")
    return {
        "niche": _clean(out.get("niche"), 200),
        "persona": _clean(out.get("persona"), 2000),
        "languages": langs or languages,
        "default_style_id": style_id if style_id in styles else None,
        "identity": _clean(out.get("identity"), 1000),
        "reason": _clean(out.get("reason"), 300),
        "provider": provider_id,
    }
