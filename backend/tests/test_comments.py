"""Caixa de comentários do YouTube: leitura simulada, filtros, lido, sugestão offline, resposta confirmada, escopo
faltando, cota contada e pendência em Hoje. Nada aqui chama o Google de verdade (httpx.MockTransport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.db import session_scope
from studio.models import Channel, CostEntry, Project, ProviderUsage, Publication, YouTubeComment
from studio.providers import credentials
from studio.workers.comments import inbox
from studio.workers.ops import routines
from studio.workers.ops.today import waiting
from studio.workers.publish import youtube as yt

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
YT_ID = "UCcanalcomentarios1234567"
SCOPE_403 = {"error": {"code": 403, "message": "Request had insufficient authentication scopes.",
                       "status": "PERMISSION_DENIED",
                       "errors": [{"message": "Insufficient Permission", "reason": "insufficientPermissions"}]}}


def _channel(cid: str = "ch_a", *, link: bool = True, token: bool = True, **settings) -> str:
    youtube = {"channel_id": YT_ID, "source": "oauth",
               "recent_videos": [{"video_id": "vid1", "title": "O mistério do farol"}]} if link else {}
    with session_scope() as s:
        s.add(Channel(id=cid, name=f"Canal {cid}", persona="Narrador calmo e curioso.", niche="mistérios",
                      settings={**({"youtube": youtube} if youtube else {}), **settings}))
    if token:
        credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
        yt.save_channel_token(YT_ID, "rt", "Canal")
    return cid


def _comment(cid: str, author: str, text: str, *, video: str = "vid1", replies: list | None = None,
             total: int | None = None, when: str = "2026-11-14T10:00:00Z", can_reply: bool = True) -> dict:
    replies = replies or []
    return {
        "id": f"th_{cid}",
        "snippet": {
            "channelId": YT_ID, "videoId": video, "canReply": can_reply, "isPublic": True,
            "totalReplyCount": len(replies) if total is None else total,
            "topLevelComment": {"id": f"c_{cid}", "snippet": {
                "authorDisplayName": author, "authorChannelId": {"value": f"UCautor{cid}"},
                "textDisplay": text, "likeCount": 3, "publishedAt": when, "updatedAt": when, "videoId": video}},
        },
        "replies": {"comments": replies},
    }


def _owner_reply() -> dict:
    return {"id": "r1", "snippet": {"authorChannelId": {"value": YT_ID}, "textDisplay": "Obrigado!"}}


def _threads() -> list[dict]:
    return [
        _comment("1", "@ana", "Como vocês descobriram isso?", when="2026-11-14T12:00:00Z"),
        _comment("2", "@bia", "Vídeo incrível, adorei", when="2026-11-14T11:00:00Z"),
        _comment("3", "@caio", "Qual a fonte disso", replies=[_owner_reply()], when="2026-11-14T10:00:00Z"),
        # Comentário do próprio canal (fixado): fica fora da caixa.
        {"id": "th_dono", "snippet": {"videoId": "vid1", "totalReplyCount": 0, "canReply": True, "topLevelComment": {
            "id": "c_dono", "snippet": {"authorChannelId": {"value": YT_ID}, "textDisplay": "Fixado: fontes na descrição",
                                        "publishedAt": "2026-11-14T09:00:00Z"}}}},
    ]


class Google:
    """Google simulado: token, commentThreads.list e comments.insert."""

    def __init__(self, threads: list[dict] | None = None, *, list_status: int = 200, insert_status: int = 200,
                 pages: int = 1, error_body: dict | None = None):
        self.threads = threads if threads is not None else _threads()
        self.list_status, self.insert_status, self.pages = list_status, insert_status, pages
        self.error_body = error_body or SCOPE_403
        self.lists: list[dict] = []
        self.inserts: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if url.startswith(f"{yt.API}/commentThreads"):
            q = parse_qs(urlparse(url).query)
            self.lists.append(q)
            if self.list_status != 200:
                return httpx.Response(self.list_status, json=self.error_body)
            page = len(self.lists)
            body: dict = {"items": self.threads}
            if page < self.pages:
                body["nextPageToken"] = f"p{page + 1}"
            return httpx.Response(200, json=body)
        if url.startswith(f"{yt.API}/comments") and request.method == "POST":
            self.inserts.append({"query": parse_qs(urlparse(url).query), "body": json.loads(request.content),
                                 "auth": request.headers.get("Authorization")})
            if self.insert_status != 200:
                return httpx.Response(self.insert_status, json=self.error_body)
            return httpx.Response(200, json={"id": "c_resposta", "snippet": {"parentId": "c_1"}})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _use(monkeypatch: pytest.MonkeyPatch, google: Google) -> None:
    monkeypatch.setattr(yt, "new_client", lambda timeout=60: google.client())


# --------------------------------------------------------------------------------------------
# Perguntas
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Como faz isso?", "qual o nome da música", "Alguém sabe onde fica.",
                                  "Where is this place", "¿Dónde queda eso", "Legal. Quando sai a parte 2"])
def test_is_question_true(text: str):
    assert inbox.is_question(text)


@pytest.mark.parametrize("text", ["", "Vídeo incrível", "Comovente demais", "Qualidade top", "first"])
def test_is_question_false(text: str):
    assert not inbox.is_question(text)


# --------------------------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------------------------


def test_refresh_reads_threads_parses_and_counts_quota(monkeypatch: pytest.MonkeyPatch):
    _channel()
    google = Google()
    _use(monkeypatch, google)
    out = inbox.refresh("ch_a", now=NOW)
    (res,) = out["channels"]
    assert res["state"] == "ok" and res["new"] == 3 and res["units"] == 1
    q = google.lists[0]
    assert q["allThreadsRelatedToChannelId"] == [YT_ID] and q["part"] == ["snippet,replies"]
    assert q["maxResults"] == ["100"] and q["order"] == ["time"] and q["textFormat"] == ["plainText"]
    with session_scope() as s:
        rows = {r.id: r for r in s.query(YouTubeComment).all()}
        assert set(rows) == {"th_1", "th_2", "th_3"}  # o comentário do próprio canal fica fora
        assert rows["th_1"].is_question and not rows["th_1"].answered and rows["th_1"].comment_id == "c_1"
        assert rows["th_1"].video_title == "O mistério do farol" and rows["th_1"].author == "@ana"
        assert not rows["th_2"].is_question
        assert rows["th_3"].answered  # o canal respondeu no YouTube
    # Cota contada no medidor do YouTube.
    quota = inbox.quota_today()
    assert quota["comments"] == 1 and quota["youtube"] >= 1
    with session_scope() as s:
        assert s.query(CostEntry).filter_by(provider="youtube", kind="comentarios").count() == 1
        assert s.query(ProviderUsage).filter_by(provider="youtube").one().units == 1


def test_refresh_keeps_local_state_and_follows_pages(monkeypatch: pytest.MonkeyPatch):
    _channel()
    # Primeira leitura: segue as páginas até o limite (MAX_PAGES), 1 unidade por página.
    google = Google(pages=5)
    _use(monkeypatch, google)
    res = inbox.refresh("ch_a", now=NOW)["channels"][0]
    assert len(google.lists) == inbox.MAX_PAGES and res["units"] == inbox.MAX_PAGES
    assert google.lists[1]["pageToken"] == ["p2"] and "pageToken" not in google.lists[0]
    inbox.mark_read(["th_2"])
    with session_scope() as s:
        s.get(YouTubeComment, "th_1").suggestion = "rascunho"
    # Segunda leitura: a 1ª página já chega em fios anteriores à última leitura, então para nela (não gasta cota à
    # toa). O que é do Vulto fica; uma resposta do canal no YouTube marca respondido.
    threads = _threads()[:3]
    threads[0] = _comment("1", "@ana", "Como vocês descobriram isso?", replies=[_owner_reply()])
    google = Google(threads, pages=5)
    _use(monkeypatch, google)
    res = inbox.refresh("ch_a", now=NOW)["channels"][0]
    assert res["new"] == 0 and res["updated"] == 3
    assert len(google.lists) == 1 and res["units"] == 1
    with session_scope() as s:
        assert s.get(YouTubeComment, "th_2").read
        th1 = s.get(YouTubeComment, "th_1")
        assert th1.suggestion == "rascunho" and th1.answered


def test_refresh_scope_missing_marks_state_and_today(monkeypatch: pytest.MonkeyPatch):
    _channel()
    _use(monkeypatch, Google(list_status=403))
    res = inbox.refresh("ch_a", now=NOW)["channels"][0]
    assert res["state"] == "sem_escopo" and "vincule de novo" in res["error"] and res["units"] == 1
    items = [w for w in waiting(NOW) if w["kind"] == "comments"]
    assert len(items) == 1 and items[0]["where"] == "/canais" and "responder comentários" in items[0]["text"]
    assert inbox.quota_today()["comments"] == 1  # a chamada recusada também conta


def test_refresh_quota_exceeded_is_error(monkeypatch: pytest.MonkeyPatch):
    _channel()
    body = {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
    _use(monkeypatch, Google(list_status=403, error_body=body))
    res = inbox.refresh("ch_a", now=NOW)["channels"][0]
    assert res["state"] == "erro" and "cota" in res["error"]


def test_refresh_without_token_or_public_link_does_not_call_google(monkeypatch: pytest.MonkeyPatch):
    _channel("ch_a", token=False)
    with session_scope() as s:
        s.add(Channel(id="ch_pub", name="Público", settings={"youtube": {"channel_id": "UCoutro", "source": "public"}}))
        s.add(Channel(id="ch_sem", name="Sem YouTube", settings={}))
    google = Google()
    _use(monkeypatch, google)
    out = inbox.refresh(now=NOW)
    states = {c["channel_id"]: c["state"] for c in out["channels"]}
    assert states == {"ch_a": "sem_token", "ch_pub": "publico"}  # canal sem YouTube nem entra
    assert google.lists == [] and inbox.quota_today()["comments"] == 0


# --------------------------------------------------------------------------------------------
# Filtros, lido e Hoje
# --------------------------------------------------------------------------------------------


def test_filters_counts_mark_read_and_today_pending(monkeypatch: pytest.MonkeyPatch):
    _channel()
    _use(monkeypatch, Google())
    inbox.refresh("ch_a", now=NOW)
    out = inbox.list_comments("ch_a", "nao_respondidos")
    assert [c["id"] for c in out["items"]] == ["th_1", "th_2"]  # mais novo primeiro
    assert out["counts"] == {"todos": 3, "nao_respondidos": 2, "perguntas": 1, "nao_lidos": 3}
    assert out["videos"] == [{"video_id": "vid1", "title": "O mistério do farol", "count": 3}]
    assert out["items"][0]["comment_url"].endswith("watch?v=vid1&lc=c_1")
    assert [c["id"] for c in inbox.list_comments(None, "perguntas")["items"]] == ["th_1"]
    with pytest.raises(ValueError):
        inbox.list_comments(None, "errado")

    items = [w for w in waiting(NOW) if w["kind"] == "comments"]
    assert len(items) == 1 and "1 pergunta sem resposta" in items[0]["text"]
    assert items[0]["where"] == "/comentarios?canal=ch_a&filtro=perguntas"

    assert inbox.mark_read(["th_1"]) == 1
    assert not [w for w in waiting(NOW) if w["kind"] == "comments"]  # lida: sai de Hoje
    assert inbox.mark_read(["th_1"], read=False) == 1
    assert inbox.mark_all_read("ch_a") == 3
    assert inbox.list_comments("ch_a", "nao_lidos")["total"] == 0


# --------------------------------------------------------------------------------------------
# Sugestão e resposta
# --------------------------------------------------------------------------------------------


def test_suggest_reply_uses_persona_and_does_not_send(monkeypatch: pytest.MonkeyPatch):
    _channel()
    google = Google()
    _use(monkeypatch, google)
    inbox.refresh("ch_a", now=NOW)
    seen: dict = {}
    from studio.workers.publish import youtube_channels

    real = youtube_channels._llm

    def spy(task, system, prompt, schema, context):
        seen.update(task=task, prompt=prompt, context=context)
        return real(task, system, prompt, schema, context)

    monkeypatch.setattr(youtube_channels, "_llm", spy)
    out = inbox.suggest_reply("th_1")
    assert seen["task"] == "comment_reply" and "Narrador calmo e curioso." in seen["prompt"]
    assert "O mistério do farol" in seen["prompt"] and seen["context"]["question"] is True
    assert out["suggestion"].startswith("Valeu, ana!") and out["suggestion_provider"]
    assert google.inserts == []  # sugerir nunca envia
    with pytest.raises(KeyError):
        inbox.suggest_reply("nao_existe")


def test_reply_sends_comments_insert_and_counts_50(monkeypatch: pytest.MonkeyPatch):
    _channel()
    google = Google()
    _use(monkeypatch, google)
    inbox.refresh("ch_a", now=NOW)
    out = inbox.reply("th_1", "  Pesquisamos em arquivos de jornal.  ", now=NOW)
    (sent,) = google.inserts
    assert sent["query"]["part"] == ["snippet"] and sent["auth"] == "Bearer at"
    assert sent["body"] == {"snippet": {"parentId": "c_1", "textOriginal": "Pesquisamos em arquivos de jornal."}}
    assert out["answered"] and out["read"] and out["reply_text"] == "Pesquisamos em arquivos de jornal."
    assert out["replied_at"] and out["reply_count"] == 1
    assert inbox.quota_today()["comments"] == 1 + inbox.REPLY_COST
    with pytest.raises(ValueError):
        inbox.reply("th_1", "   ")


def test_reply_scope_missing_marks_channel(monkeypatch: pytest.MonkeyPatch):
    _channel()
    _use(monkeypatch, Google())
    inbox.refresh("ch_a", now=NOW)
    _use(monkeypatch, Google(insert_status=403))
    with pytest.raises(inbox.ScopeMissing):
        inbox.reply("th_1", "Oi", now=NOW)
    assert inbox.channel_states("ch_a")[0]["state"] == "sem_escopo"
    with session_scope() as s:
        assert not s.get(YouTubeComment, "th_1").answered


def test_reply_refuses_when_cannot_reply(monkeypatch: pytest.MonkeyPatch):
    _channel()
    google = Google([_comment("9", "@z", "Por quê?", can_reply=False)])
    _use(monkeypatch, google)
    inbox.refresh("ch_a", now=NOW)
    with pytest.raises(ValueError):
        inbox.reply("th_9", "Oi")
    assert google.inserts == []
    assert not [w for w in waiting(NOW) if w["kind"] == "comments"]  # sem como responder: não vira pendência


# --------------------------------------------------------------------------------------------
# Títulos dos vídeos publicados pelo Vulto e rotina da manhã
# --------------------------------------------------------------------------------------------


def test_title_from_vulto_publication(monkeypatch: pytest.MonkeyPatch):
    _channel()
    with session_scope() as s:
        s.add(Project(id="prj_1", title="Projeto do farol 2", channel_id="ch_a", style_id="x"))
        s.add(Publication(id="pub_1", project_id="prj_1", platform="youtube", language="pt-BR", driver="youtube_native",
                          status="uploaded", external_id="vid2"))
    _use(monkeypatch, Google([_comment("5", "@d", "Top", video="vid2")]))
    inbox.refresh("ch_a", now=NOW)
    assert inbox.list_comments("ch_a", "todos")["items"][0]["video_title"] == "Projeto do farol 2"


def test_morning_reads_only_channels_already_using_the_inbox(monkeypatch: pytest.MonkeyPatch):
    _channel()
    google = Google()
    _use(monkeypatch, google)
    assert routines.step_comments(NOW) == []  # caixa nunca usada: a rotina não gasta cota
    assert google.lists == []
    inbox.refresh("ch_a", now=NOW)
    lines = routines.step_comments(NOW)
    assert len(google.lists) == 2 and lines == []  # nada novo na segunda leitura


# --------------------------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------------------------


def test_routes_end_to_end(monkeypatch: pytest.MonkeyPatch):
    _channel()
    google = Google()
    _use(monkeypatch, google)
    client = TestClient(create_app())

    res = client.post("/api/comments/refresh", json={"channel_id": "ch_a"})
    assert res.status_code == 200 and res.json()["new"] == 3 and res.json()["quota"]["comments"] == 1
    assert client.post("/api/comments/refresh", json={"channel_id": "nao"}).status_code == 404

    body = client.get("/api/comments", params={"channel_id": "ch_a", "filter": "perguntas"}).json()
    assert [c["id"] for c in body["items"]] == ["th_1"] and body["channels"][0]["state"] == "ok"
    assert body["quota"]["daily"] == 10_000
    assert client.get("/api/comments", params={"filter": "errado"}).status_code == 422

    sug = client.post("/api/comments/th_1/suggest").json()
    assert sug["suggestion"]
    assert client.post("/api/comments/nao/suggest").status_code == 404

    # Sem confirmação nada vai ao YouTube.
    res = client.post("/api/comments/th_1/reply", json={"text": "Oi"})
    assert res.status_code == 409 and google.inserts == []
    res = client.post("/api/comments/th_1/reply", json={"text": sug["suggestion"], "confirm": True})
    assert res.status_code == 200 and res.json()["comment"]["answered"] and len(google.inserts) == 1
    assert res.json()["quota"]["comments"] == 51

    assert client.post("/api/comments/read", json={"ids": ["th_2"]}).json() == {"changed": 1}
    assert client.post("/api/comments/read-all", json={"channel_id": "ch_a"}).json() == {"changed": 1}

    _use(monkeypatch, Google(insert_status=403))
    res = client.post("/api/comments/th_2/reply", json={"text": "Oi", "confirm": True})
    assert res.status_code == 403 and "vincule de novo" in res.json()["detail"]
    _use(monkeypatch, Google(insert_status=400, error_body={"error": {"code": 400, "message": "x",
                                                                      "errors": [{"reason": "commentTextTooLong"}]}}))
    res = client.post("/api/comments/th_2/reply", json={"text": "Oi", "confirm": True})
    assert res.status_code == 502 and "longa demais" in res.json()["detail"]
