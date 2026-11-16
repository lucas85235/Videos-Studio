"""Caixa de comentários do YouTube (README §10.1, Fase 5).

- Leitura: `commentThreads.list` com `allThreadsRelatedToChannelId` (fios de todos os vídeos do canal), `part=snippet,
  replies`, 100 por página, do mais novo para o mais antigo. Custa 1 unidade de cota por página.
- Resposta: `comments.insert` com `snippet.parentId` = comentário principal do fio. Custa 50 unidades e exige o escopo
  `youtube.force-ssl`, que já está nos escopos do Vulto desde o primeiro envio. Token sem ele (403
  insufficientPermissions) não é erro mudo: o canal fica "sem escopo" e a tela (e Hoje) pedem para vincular de novo,
  como faz a coleta de métricas.
- A resposta só sai depois de você confirmar na tela (`confirm=True`); a IA só sugere o texto, no tom da persona do
  canal, e nada vai ao YouTube sem você.
- "Lido", a sugestão e a resposta enviada ficam só no Vulto (a API não tem "marcar como lido").
- "Respondido": o canal tem resposta no fio (entre as respostas que a API devolve junto, ou enviada pelo app). Num fio
  com muitas respostas a API devolve só parte delas; se a do canal não estiver entre elas, o fio aparece como não
  respondido até você marcar como lido.

Toda chamada soma a cota gasta no medidor do YouTube (`ProviderUsage`/`CostEntry`, provedor "youtube", tipo
"comentarios"). A cota é por projeto do Google Cloud: todos os canais dividem as 10.000 unidades do dia.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import func

from studio.db import session_scope
from studio.models import Channel, CostEntry, Project, ProviderUsage, Publication, YouTubeComment
from studio.workers.publish import youtube as yt

log = logging.getLogger(__name__)

LIST_COST = 1  # commentThreads.list, por página
REPLY_COST = 50  # comments.insert
DAILY_QUOTA = 10_000  # unidades por dia, por projeto do Google Cloud
PAGE_SIZE = 100
MAX_PAGES = 3  # até 300 fios por leitura (3 unidades)
REPLY_MAX = 10_000  # o YouTube não publica o limite; textos maiores voltam como commentTextTooLong
QUOTA_KIND = "comentarios"

# Estados da caixa por canal.
OK, SEM_ESCOPO, SEM_TOKEN, PUBLICO, ERRO = "ok", "sem_escopo", "sem_token", "publico", "erro"
FILTERS = ("todos", "nao_respondidos", "perguntas", "nao_lidos")

_SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "RESOURCE_EXHAUSTED"}


class ScopeMissing(yt.YouTubeError):
    """O token do canal não tem o escopo `youtube.force-ssl` (vincular de novo resolve)."""


# --------------------------------------------------------------------------------------------
# Perguntas
# --------------------------------------------------------------------------------------------

_QUESTION_START = re.compile(
    r"^\s*(como|quando|onde|aonde|por ?que|porque|qual|quais|quem|quanto|quantos|quantas|o que|oq|vc sabe|voc[eê] sabe|"
    r"algu[eé]m sabe|tem como|d[aá] pra|da para|existe|ser[aá] que|pode|poderia|"
    r"how|what|why|when|where|who|which|can|could|is there|are there|do you|does|did|will|would|"
    r"c[oó]mo|qu[eé]|cu[aá]ndo|d[oó]nde|por qu[eé]|cu[aá]l|qui[eé]n|cu[aá]nto)\b",
    re.IGNORECASE,
)


def is_question(text: str) -> bool:
    """Pergunta: tem "?" (inclusive o "¿" do espanhol) ou alguma frase começa com palavra de pergunta."""
    if not text:
        return False
    if "?" in text or "¿" in text:
        return True
    return any(_QUESTION_START.match(part) for part in re.split(r"[.!\n]+", text) if part.strip())


# --------------------------------------------------------------------------------------------
# Cota
# --------------------------------------------------------------------------------------------


def _spend(units: int) -> None:
    """Soma a cota gasta no medidor do YouTube (mesmo provedor do envio: a cota é do projeto do Google Cloud)."""
    if units <= 0:
        return
    from studio.providers import registry

    registry.record_success("youtube", QUOTA_KIND, float(units), "unidades de cota", 0.0)


def quota_today() -> dict[str, Any]:
    """Cota do YouTube gasta hoje: pela caixa de comentários e no total que o Vulto contou."""
    today = date.today()
    start = datetime.combine(today, datetime.min.time()).astimezone(UTC)
    with session_scope() as s:
        comments = s.query(func.coalesce(func.sum(CostEntry.units), 0.0)).filter(
            CostEntry.provider == "youtube", CostEntry.kind == QUOTA_KIND, CostEntry.created_at >= start).scalar()
        row = s.query(ProviderUsage).filter_by(provider="youtube", day=today.isoformat()).one_or_none()
        total = row.units if row else 0.0
    return {"comments": int(comments or 0), "youtube": int(total or 0), "daily": DAILY_QUOTA,
            "list_cost": LIST_COST, "reply_cost": REPLY_COST}


# --------------------------------------------------------------------------------------------
# Chamadas ao YouTube
# --------------------------------------------------------------------------------------------


def _reasons(resp: httpx.Response) -> set[str]:
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        return set()
    if not isinstance(err, dict):
        return set()
    out = {str(e.get("reason")) for e in err.get("errors") or [] if isinstance(e, dict) and e.get("reason")}
    out |= {str(d.get("reason")) for d in err.get("details") or [] if isinstance(d, dict) and d.get("reason")}
    if err.get("status"):
        out.add(str(err["status"]))
    return out


def _message(resp: httpx.Response) -> str:
    try:
        return str(((resp.json() or {}).get("error") or {}).get("message") or resp.text[:300])
    except (ValueError, AttributeError):
        return resp.text[:300]


def _error(resp: httpx.Response, action: str) -> yt.YouTubeError:
    """Erro do YouTube em texto simples. Escopo faltando vira `ScopeMissing`."""
    reasons = _reasons(resp)
    if resp.status_code == 401 or reasons & _SCOPE_REASONS:
        return ScopeMissing("o acesso do canal não tem a permissão de comentários; vincule de novo em Canais")
    if reasons & _QUOTA_REASONS:
        return yt.YouTubeError("a cota do YouTube de hoje acabou; tente de novo amanhã")
    if "commentsDisabled" in reasons:
        return yt.YouTubeError("os comentários estão desativados neste canal ou vídeo")
    if "operationNotSupported" in reasons:
        return yt.YouTubeError("o YouTube não deixa responder este comentário")
    if "parentCommentIsPrivate" in reasons:
        return yt.YouTubeError("o comentário é privado e não aceita resposta")
    if "parentCommentNotFound" in reasons or resp.status_code == 404:
        return yt.YouTubeError("o comentário não existe mais no YouTube")
    if "commentTextTooLong" in reasons:
        return yt.YouTubeError("a resposta está longa demais para o YouTube; encurte o texto")
    if "ineligibleAccount" in reasons:
        return yt.YouTubeError("a conta do YouTube precisa estar ligada a uma conta Google para comentar")
    if "forbidden" in reasons:
        return yt.YouTubeError(f"o YouTube recusou {action} (sem permissão neste canal)")
    return yt.YouTubeError(f"erro do YouTube ao {action} ({resp.status_code}): {_message(resp)}")


def _with_units(exc: yt.YouTubeError, units: int) -> yt.YouTubeError:
    exc.units = units  # type: ignore[attr-defined]  # cota já gasta antes da falha
    return exc


def fetch_threads(http: httpx.Client, token: str, youtube_id: str, *, max_pages: int = MAX_PAGES,
                  stop_before: datetime | None = None) -> tuple[list[dict[str, Any]], int]:
    """Fios de comentários de todos os vídeos do canal, do mais novo para o mais antigo. Devolve (fios, unidades).

    `stop_before`: para de pedir páginas quando a página já chega em fios atualizados antes dessa hora (já lidos
    numa leitura anterior), para não gastar cota à toa.
    """
    items: list[dict[str, Any]] = []
    units = 0
    page_token = ""
    for _ in range(max(1, max_pages)):
        params = {"part": "snippet,replies", "allThreadsRelatedToChannelId": youtube_id, "maxResults": str(PAGE_SIZE),
                  "order": "time", "textFormat": "plainText"}
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = http.get(f"{yt.API}/commentThreads", params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            _spend(units)
            raise _with_units(yt.YouTubeError(f"sem resposta do YouTube: {exc}"), units) from exc
        units += LIST_COST  # a chamada conta na cota mesmo quando dá erro
        if resp.status_code != 200:
            _spend(units)
            raise _with_units(_error(resp, "ler os comentários"), units)
        data = resp.json()
        page = data.get("items") or []
        items.extend(page)
        page_token = data.get("nextPageToken") or ""
        if not page_token or not page:
            break
        if stop_before is not None:
            last = _when(((page[-1].get("snippet") or {}).get("topLevelComment") or {}).get("snippet", {}).get("updatedAt"))
            if last is not None and last < stop_before:
                break
    _spend(units)
    return items, units


def insert_reply(http: httpx.Client, token: str, parent_id: str, text: str) -> dict[str, Any]:
    """comments.insert (50 unidades): responde ao comentário principal `parent_id`."""
    body = {"snippet": {"parentId": parent_id, "textOriginal": text}}
    try:
        resp = http.post(f"{yt.API}/comments", params={"part": "snippet"}, json=body,
                         headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise yt.YouTubeError(f"sem resposta do YouTube: {exc}") from exc
    _spend(REPLY_COST)
    if resp.status_code not in (200, 201):
        raise _error(resp, "enviar a resposta")
    return resp.json()


# --------------------------------------------------------------------------------------------
# Banco
# --------------------------------------------------------------------------------------------


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _author_id(snippet: dict[str, Any]) -> str:
    author = snippet.get("authorChannelId") or {}
    return str(author.get("value") or "") if isinstance(author, dict) else str(author or "")


def parse_thread(item: dict[str, Any], youtube_id: str) -> dict[str, Any] | None:
    """Campos de um fio (commentThread). Comentário do próprio canal (fixado, aviso) fica de fora."""
    snippet = item.get("snippet") or {}
    top = snippet.get("topLevelComment") or {}
    top_snippet = top.get("snippet") or {}
    thread_id = item.get("id") or ""
    if not thread_id:
        return None
    author_id = _author_id(top_snippet)
    if youtube_id and author_id == youtube_id:
        return None
    replies = (item.get("replies") or {}).get("comments") or []
    answered = any(_author_id(r.get("snippet") or {}) == youtube_id for r in replies if isinstance(r, dict))
    text = str(top_snippet.get("textDisplay") or top_snippet.get("textOriginal") or "").strip()
    return {
        "id": thread_id,
        "video_id": str(snippet.get("videoId") or top_snippet.get("videoId") or ""),
        "comment_id": str(top.get("id") or thread_id),
        "author": str(top_snippet.get("authorDisplayName") or "")[:200],
        "author_channel_id": author_id[:40],
        "text": text,
        "like_count": _int(top_snippet.get("likeCount")),
        "reply_count": _int(snippet.get("totalReplyCount")),
        "can_reply": bool(snippet.get("canReply", True)),
        "is_question": is_question(text),
        "answered": answered,
        "published_at": _when(top_snippet.get("publishedAt")),
        "updated_at": _when(top_snippet.get("updatedAt")),
    }


def _video_titles(s: Any, channel_id: str, settings: dict[str, Any]) -> dict[str, str]:
    """Títulos dos vídeos do canal: vídeos recentes, coleta de métricas e projetos publicados pelo Vulto."""
    titles: dict[str, str] = {}
    for v in ((settings.get("youtube") or {}).get("recent_videos") or []):
        if isinstance(v, dict) and v.get("video_id"):
            titles[v["video_id"]] = v.get("title") or ""
    for vid, info in (((settings.get("analytics") or {}).get("videos")) or {}).items():
        if isinstance(info, dict) and info.get("title"):
            titles[vid] = info["title"]
    rows = (
        s.query(Publication.external_id, Project.title)
        .join(Project, Project.id == Publication.project_id)
        .filter(Project.channel_id == channel_id, Publication.platform == "youtube", Publication.external_id.isnot(None))
        .all()
    )
    for vid, title in rows:
        titles.setdefault(vid, title or "")
    return titles


def comment_dict(row: YouTubeComment) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "video_id": row.video_id,
        "video_title": row.video_title,
        "video_url": f"{yt.SITE}/watch?v={row.video_id}" if row.video_id else "",
        "comment_url": f"{yt.SITE}/watch?v={row.video_id}&lc={row.comment_id}" if row.video_id else "",
        "author": row.author,
        "text": row.text,
        "like_count": row.like_count,
        "reply_count": row.reply_count,
        "can_reply": row.can_reply,
        "is_question": row.is_question,
        "answered": row.answered,
        "read": row.read,
        "suggestion": row.suggestion,
        "suggestion_provider": row.suggestion_provider,
        "reply_text": row.reply_text,
        "replied_at": row.replied_at.isoformat() if row.replied_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }


def _save_state(channel_id: str, **state: Any) -> dict[str, Any]:
    """Estado da caixa em `Channel.settings["comments"]` (sem mexer no resto)."""
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            return {}
        settings = dict(channel.settings or {})
        current = {**(settings.get("comments") or {}), **state}
        settings["comments"] = current
        channel.settings = settings  # atribuição nova: o SQLAlchemy só vê a troca do JSON inteiro
        return dict(current)


def store_threads(channel_id: str, youtube_id: str, items: list[dict[str, Any]], now: datetime) -> dict[str, int]:
    """Grava os fios. O que é só do Vulto (lido, sugestão, resposta pelo app) não muda; um fio que ganhou resposta do
    canal no YouTube passa a respondido. Devolve quantos são novos e quantos foram atualizados."""
    new = updated = 0
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        titles = _video_titles(s, channel_id, dict(channel.settings or {})) if channel else {}
        for item in items:
            data = parse_thread(item, youtube_id)
            if data is None:
                continue
            row = s.get(YouTubeComment, data["id"])
            if row is None:
                row = YouTubeComment(id=data["id"], channel_id=channel_id, read=False, suggestion="",
                                     suggestion_provider="", reply_text="", reply_id="")
                s.add(row)
                new += 1
            else:
                updated += 1
                data["answered"] = data["answered"] or row.answered
            for key, value in data.items():
                if key != "id":
                    setattr(row, key, value)
            row.channel_id = channel_id
            row.youtube_channel_id = youtube_id
            row.video_title = titles.get(row.video_id, row.video_title or "")[:300]
            row.fetched_at = now
    return {"new": new, "updated": updated}


# --------------------------------------------------------------------------------------------
# Leitura (um canal ou todos)
# --------------------------------------------------------------------------------------------


def _token(http: httpx.Client, channel_id: str) -> tuple[str, str]:
    """(token de acesso, credencial usada) do canal; `YouTubeError` com o motivo se não houver acesso."""
    account = yt.account_for_channel(channel_id)
    return yt.access_token(http, account), account


def refresh_channel(channel_id: str, http: httpx.Client, now: datetime | None = None) -> dict[str, Any]:
    """Lê os comentários novos de um canal. Devolve o resumo (`state`, `new`, `updated`, `units`)."""
    now = now or datetime.now(UTC)
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            raise KeyError(channel_id)
        name = channel.name
        link = dict((channel.settings or {}).get("youtube") or {})
        prev = dict((channel.settings or {}).get("comments") or {})
    youtube_id = link.get("channel_id") or ""
    base = {"channel_id": channel_id, "name": name, "new": 0, "updated": 0, "units": 0}
    if not youtube_id:
        saved = _save_state(channel_id, state=SEM_TOKEN, error="o canal não está ligado ao YouTube",
                            checked_at=now.isoformat())
        return {**base, **saved}
    try:
        token, _ = _token(http, channel_id)
    except yt.YouTubeError as exc:
        state = PUBLICO if link.get("source") == "public" else SEM_TOKEN
        saved = _save_state(channel_id, state=state, error=str(exc), checked_at=now.isoformat())
        return {**base, **saved}
    stop = _when(prev.get("last_run"))
    try:
        items, units = fetch_threads(http, token, youtube_id, stop_before=stop)
    except ScopeMissing as exc:
        saved = _save_state(channel_id, state=SEM_ESCOPO, error=str(exc), checked_at=now.isoformat())
        return {**base, "units": getattr(exc, "units", 0), **saved}
    except yt.YouTubeError as exc:
        saved = _save_state(channel_id, state=ERRO, error=str(exc), checked_at=now.isoformat())
        return {**base, "units": getattr(exc, "units", 0), **saved}
    counts = store_threads(channel_id, youtube_id, items, now)
    saved = _save_state(channel_id, state=OK, error="", last_run=now.isoformat(), checked_at=now.isoformat())
    return {**base, **counts, "units": units, **saved}


def refresh(channel_id: str | None = None, *, http: httpx.Client | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    """Lê os comentários de um canal ou de todos os canais ligados ao YouTube."""
    with session_scope() as s:
        if channel_id:
            if s.get(Channel, channel_id) is None:
                raise KeyError(channel_id)
            ids = [channel_id]
        else:
            ids = [c.id for c in s.query(Channel).order_by(Channel.name).all()
                   if ((c.settings or {}).get("youtube") or {}).get("channel_id")]
    own = http is None
    client = http or yt.new_client()
    try:
        out = [refresh_channel(cid, client, now) for cid in ids]
    finally:
        if own:
            client.close()
    return {"channels": out, "new": sum(c.get("new", 0) for c in out), "units": sum(c.get("units", 0) for c in out)}


# --------------------------------------------------------------------------------------------
# Lista, lido e contagens
# --------------------------------------------------------------------------------------------


def _query(s: Any, channel_id: str | None, flt: str, video_id: str | None):
    q = s.query(YouTubeComment)
    if channel_id:
        q = q.filter(YouTubeComment.channel_id == channel_id)
    if video_id:
        q = q.filter(YouTubeComment.video_id == video_id)
    if flt == "nao_respondidos":
        q = q.filter(YouTubeComment.answered.is_(False))
    elif flt == "perguntas":
        q = q.filter(YouTubeComment.is_question.is_(True), YouTubeComment.answered.is_(False))
    elif flt == "nao_lidos":
        q = q.filter(YouTubeComment.read.is_(False))
    return q


def list_comments(channel_id: str | None = None, flt: str = "nao_respondidos", video_id: str | None = None,
                  limit: int = 100, offset: int = 0) -> dict[str, Any]:
    if flt not in FILTERS:
        raise ValueError(f"filtro inválido: {flt}")
    with session_scope() as s:
        q = _query(s, channel_id, flt, video_id)
        total = q.count()
        rows = (q.order_by(YouTubeComment.published_at.desc(), YouTubeComment.id)
                .offset(max(0, offset)).limit(max(1, min(limit, 500))).all())
        items = [comment_dict(r) for r in rows]
        counts = {f: _query(s, channel_id, f, video_id).count() for f in FILTERS}
        videos = (
            s.query(YouTubeComment.video_id, YouTubeComment.video_title, func.count(YouTubeComment.id))
            .filter(*([YouTubeComment.channel_id == channel_id] if channel_id else []))
            .group_by(YouTubeComment.video_id, YouTubeComment.video_title)
            .order_by(func.max(YouTubeComment.published_at).desc())
            .all()
        )
    return {"items": items, "total": total, "counts": counts,
            "videos": [{"video_id": v, "title": t or v, "count": n} for v, t, n in videos if v]}


def channel_states(channel_id: str | None = None) -> list[dict[str, Any]]:
    """Estado da caixa por canal ligado ao YouTube: ok | sem_escopo | sem_token | publico | erro | nunca."""
    with session_scope() as s:
        q = s.query(Channel).order_by(Channel.name)
        if channel_id:
            q = q.filter(Channel.id == channel_id)
        out = []
        for c in q.all():
            settings = c.settings or {}
            if not (settings.get("youtube") or {}).get("channel_id"):
                continue
            state = settings.get("comments") or {}
            out.append({"channel_id": c.id, "name": c.name, "state": state.get("state") or "nunca",
                        "error": state.get("error") or "", "last_run": state.get("last_run")})
        return out


def mark_read(ids: list[str], read: bool = True) -> int:
    with session_scope() as s:
        rows = s.query(YouTubeComment).filter(YouTubeComment.id.in_(ids)).all() if ids else []
        for row in rows:
            row.read = read
        return len(rows)


def mark_all_read(channel_id: str | None = None, flt: str = "todos") -> int:
    if flt not in FILTERS:
        raise ValueError(f"filtro inválido: {flt}")
    with session_scope() as s:
        rows = _query(s, channel_id, flt, None).filter(YouTubeComment.read.is_(False)).all()
        for row in rows:
            row.read = True
        return len(rows)


def open_questions() -> list[dict[str, Any]]:
    """Perguntas sem resposta e ainda não lidas, por canal (para a página Hoje)."""
    with session_scope() as s:
        rows = (
            s.query(Channel.id, Channel.name, func.count(YouTubeComment.id))
            .join(YouTubeComment, YouTubeComment.channel_id == Channel.id)
            .filter(YouTubeComment.is_question.is_(True), YouTubeComment.answered.is_(False),
                    YouTubeComment.read.is_(False), YouTubeComment.can_reply.is_(True))
            .group_by(Channel.id, Channel.name)
            .order_by(Channel.name)
            .all()
        )
    return [{"channel_id": cid, "name": name, "count": n} for cid, name, n in rows if n]


# --------------------------------------------------------------------------------------------
# Sugestão da IA e resposta
# --------------------------------------------------------------------------------------------

REPLY_SYSTEM = """Você escreve respostas curtas a comentários do YouTube em nome do narrador de um canal sem rosto.
Regras:
- responda no tom da persona do canal e no mesmo idioma do comentário;
- 1 a 3 frases, simples e humanas; sem hashtags, sem links e sem pedir inscrição;
- se for uma pergunta, responda só com o que o título do vídeo e o comentário permitem saber; se não der para saber,
  diga com honestidade que vai conferir ou sugira um vídeo futuro, sem inventar fatos, números ou datas;
- crítica ou ofensa: responda com calma e educação, sem discutir; spam ou golpe: devolva reply vazio;
- nunca prometa nada em nome do canal (sorteio, dinheiro, parceria) e nunca peça dados pessoais.
Devolva só o texto da resposta em `reply`."""


def _reply_schema() -> dict[str, Any]:
    from studio.workers.pipeline.schemas import S, _obj

    return _obj({"reply": S})


def suggest_reply(comment_id: str) -> dict[str, Any]:
    """Pede à IA uma resposta no tom da persona do canal e guarda como sugestão (não envia nada)."""
    from studio.workers.publish.youtube_channels import _llm

    with session_scope() as s:
        row = s.get(YouTubeComment, comment_id)
        if row is None:
            raise KeyError(comment_id)
        channel = s.get(Channel, row.channel_id)
        name = channel.name if channel else ""
        persona = (channel.persona or "") if channel else ""
        niche = (channel.niche or "") if channel else ""
        languages = list(channel.languages or ["pt-BR"]) if channel else ["pt-BR"]
        text, author, video_title = row.text, row.author, row.video_title
    prompt = "\n".join([
        f"Canal: {name}",
        f"Nicho: {niche or '(não informado)'}",
        f"Persona do narrador: {persona or '(não informada; use um tom simpático e direto)'}",
        f"Idioma principal do canal: {languages[0]}",
        f"Vídeo: {video_title or '(título desconhecido)'}",
        f"Autor do comentário: {author or '(anônimo)'}",
        "Comentário:",
        text,
    ])
    context = {"text": text, "author": author, "persona": persona, "channel": name, "video_title": video_title,
               "language": languages[0], "question": is_question(text)}
    result, provider_id = _llm("comment_reply", REPLY_SYSTEM, prompt, _reply_schema(), context)
    suggestion = str((result.data or {}).get("reply") or "").strip()[:2000]
    with session_scope() as s:
        row = s.get(YouTubeComment, comment_id)
        if row is None:
            raise KeyError(comment_id)
        row.suggestion = suggestion
        row.suggestion_provider = provider_id
        return comment_dict(row)


def reply(comment_id: str, text: str, *, http: httpx.Client | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Envia a resposta ao YouTube (comments.insert). A rota só chama depois da confirmação do usuário."""
    text = (text or "").strip()
    if not text:
        raise ValueError("escreva a resposta antes de enviar")
    if len(text) > REPLY_MAX:
        raise ValueError("a resposta está longa demais")
    now = now or datetime.now(UTC)
    with session_scope() as s:
        row = s.get(YouTubeComment, comment_id)
        if row is None:
            raise KeyError(comment_id)
        if not row.can_reply:
            raise ValueError("o YouTube não deixa responder este comentário")
        channel_id, parent_id = row.channel_id, row.comment_id
    own = http is None
    client = http or yt.new_client()
    try:
        token, _ = _token(client, channel_id)
        try:
            created = insert_reply(client, token, parent_id, text)
        except ScopeMissing as exc:
            _save_state(channel_id, state=SEM_ESCOPO, error=str(exc), checked_at=now.isoformat())
            raise
    finally:
        if own:
            client.close()
    with session_scope() as s:
        row = s.get(YouTubeComment, comment_id)
        if row is None:  # apagado enquanto enviava: a resposta já está no YouTube
            raise KeyError(comment_id)
        row.reply_text = text
        row.reply_id = str(created.get("id") or "")
        row.replied_at = now
        row.answered = True
        row.read = True
        row.reply_count = (row.reply_count or 0) + 1
        return comment_dict(row)
