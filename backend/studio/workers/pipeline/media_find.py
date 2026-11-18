"""Busca automática de arte oficial e notícias para as sugestões de mídia real do storyboard (job `media_find`).

A busca de fotos livres (`providers.media_search`: Commons, Openverse, Pexels) não acha arte oficial nem a imagem da
notícia. Aqui o Claude Code com WebSearch e WebFetch procura os candidatos de TODAS as sugestões pendentes numa
chamada só (uma por vídeo: é o que custa). Cada candidato é conferido sem LLM: só https, imagem de verdade e grande o
bastante (com a og:image da página de reserva). O documento guarda só os endereços (`RealHint.found`); o arquivo é
baixado quando o usuário anexa, pela rota de mídia.

Sem o Claude Code com busca web como 1º LLM da cadeia, nada é chamado (outro modelo inventaria os links).
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, date, datetime
from typing import Any

import httpx

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import FoundMedia, ProjectDoc, RealHint, Scene
from studio.core.sources import looks_synthetic
from studio.core.styles.library import get_style
from studio.media import link_preview, safe_http
from studio.providers import registry
from studio.providers.base import LLMResult, ProviderError
from studio.providers.llm import web_search_status
from studio.workers.pipeline.build import BuildCancelled

log = logging.getLogger(__name__)

TASK = "media_find"
WEB_PROVIDER = "claude_code"
SEARCH_KINDS = ("official", "news")
MAX_HINTS = 8
"""Sugestões por chamada (o resto fica para a próxima busca)."""
MAX_PER_SCENE = 3
ASK_PER_SCENE = 3
MIN_LONG_SIDE = 600
"""Imagem com o lado maior menor que isto não serve nem de cartão."""
PROBE_BYTES = 512_000
"""Basta o começo do arquivo para ler as dimensões (o cabeçalho do JPEG/PNG/WebP)."""
VALIDATE_WORKERS = 6
VALIDATE_BUDGET_S = 60.0
NARRATION_MAX = 500
URL_MAX = 2000
UNAVAILABLE_MESSAGE = ("A busca de arte oficial e notícias precisa do Claude Code com busca na web (WebSearch e "
                       "WebFetch) como LLM em uso")

SYSTEM = """Você acha mídia real (arte oficial, imagem de notícia, foto) para cenas de um vídeo do YouTube. Para cada
cena do pedido, devolva até 3 candidatos, cada um com:
- image: endereço DIRETO da imagem (jpg, png ou webp), o maior disponível;
- page_url: a página onde a imagem está (para "news", a própria notícia; use o link da fonte quando vier no pedido);
- outlet: o site ou veículo ("Rockstar Games", "PC Gamer"); title: descrição curta da imagem ou a manchete;
- why: uma frase curta em português dizendo por que serve para a cena;
- made: o que a imagem é (regra 2).

Regras:
1. Fonte oficial primeiro (sala de imprensa ou página de mídia do estúdio/editora/empresa, newswire oficial); depois
   veículos de imprensa conhecidos (a imagem principal/og:image da matéria, só quando ela é foto, print ou arte
   oficial do estúdio, nunca a ilustração que o próprio site fez para a matéria).
2. Nunca banco de imagens com marca d'água, fan art, sites de wallpaper, Pinterest nem imagem gerada por IA. A
   imagem de capa de blog ou matéria que é ilustração (arte editorial, montagem, imagem de IA imitando o logotipo, a
   capa ou os personagens oficiais) também não serve: logotipo, capa e personagem só da fonte oficial do estúdio. Em
   cada candidato, diga em made o que a imagem é: photo (foto real), official (arte ou quadro oficial do estúdio),
   screenshot (print de página, app ou jogo) ou illustration (qualquer outra arte); illustration é descartada.
3. A imagem tem de mostrar o que a nota da cena pede.
4. Prefira imagem grande e na orientação do vídeo.
5. Nunca invente URL: só endereços que apareceram na busca ou nas páginas que você abriu. Sem candidato bom, devolva
   a cena com a lista vazia.
6. Seja econômico: poucas buscas, abra só as páginas necessárias."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "image": {"type": "string"},
                                "page_url": {"type": "string"},
                                "title": {"type": "string"},
                                "outlet": {"type": "string"},
                                "why": {"type": "string"},
                                "made": {"type": "string", "enum": ["photo", "official", "screenshot",
                                                                    "illustration"]},
                            },
                            "required": ["image", "page_url", "title", "outlet", "why", "made"],
                        },
                    },
                },
                "required": ["scene_id", "candidates"],
            },
        }
    },
    "required": ["scenes"],
}


class MediaFindError(ProviderError):
    """A busca na web falhou (mensagem em português, vai para o job)."""


# --------------------------------------------------------------------------------------------
# Sugestões pendentes
# --------------------------------------------------------------------------------------------


def _orientation(doc: ProjectDoc) -> str:
    try:
        fmt = get_style(doc.style_id, doc.style_overrides).format
        width, height = fmt.width, fmt.height
    except Exception:  # noqa: BLE001 - sem estilo: horizontal
        return "landscape"
    return "landscape" if width > height else "portrait" if height > width else "square"


def _photo_search_empty(query: str, orientation: str) -> bool:
    """A busca de fotos livres já rodou para esta consulta e não achou nada (lê só o cache, sem rede)."""
    from studio.core.graph.store import ArtifactStore
    from studio.media.safe_http import wikimedia_contact
    from studio.providers import media_search

    text = " ".join((query or "").split())[:120]
    if not text:
        return False
    try:
        key = media_search._cache_key(text, orientation, bool(media_search._pexels_key()),
                                      wikimedia_contact() is not None)
        cached = ArtifactStore().lookup(media_search.CACHE_KIND, key)
    except Exception:  # noqa: BLE001 - sem cache: não entra
        return False
    return bool(cached) and not (cached.get("candidates") or [])


def _wants(scene: Scene, *, refresh: bool, orientation: str) -> bool:
    hint = scene.real
    if hint is None or scene.locked or scene.visual.media is not None:
        return False
    if hint.searched and not refresh:
        return False
    if hint.kind in SEARCH_KINDS:
        return True
    # Foto: só quando a busca de fotos livres já rodou e voltou vazia.
    return hint.kind == "photo" and _photo_search_empty(hint.query, orientation)


def pending_scenes(doc: ProjectDoc, *, refresh: bool = False) -> list[Scene]:
    """Cenas com sugestão oficial/notícia (ou foto sem resultado livre), sem mídia anexada e ainda não buscadas.

    Com `refresh`, entram também as já buscadas: primeiro as nunca buscadas, depois as buscadas há mais tempo (cada
    busca de novo passa a vez para as que ficaram de fora da anterior), na ordem do vídeo dentro de cada grupo."""
    orientation = _orientation(doc)
    scenes = [s for s in doc.ordered_scenes() if _wants(s, refresh=refresh, orientation=orientation)]
    if refresh:
        scenes.sort(key=lambda s: (bool(s.real and s.real.searched), s.real.searched_at if s.real else ""))
    return scenes


def has_pending(doc: ProjectDoc) -> bool:
    return any(s.real is not None and s.real.kind in SEARCH_KINDS and not s.real.searched
               and s.visual.media is None and not s.locked for s in doc.scenes)


# --------------------------------------------------------------------------------------------
# Pedido ao LLM
# --------------------------------------------------------------------------------------------


def _clip(text: object, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def build_context(doc: ProjectDoc, scenes: list[Scene], *, today: str | None = None) -> dict[str, Any]:
    lang = doc.primary_language
    hints: list[dict[str, Any]] = []
    for scene in scenes:
        hint = scene.real
        assert hint is not None
        try:
            narration = doc.segment(scene.segment_id).text_for(lang)
        except KeyError:
            narration = ""
        entry: dict[str, Any] = {"scene_id": scene.id, "kind": hint.kind, "note": _clip(hint.note, 300),
                                 "query": _clip(hint.query, 160), "narration": _clip(narration, NARRATION_MAX)}
        if hint.source_ref and 0 < hint.source_ref <= len(doc.sources):
            source = doc.sources[hint.source_ref - 1]
            entry["source"] = {"title": _clip(source.title, 200), "url": source.url}
        hints.append(entry)
    return {"title": doc.title, "topic": _clip(doc.topic, 300), "today": today or date.today().isoformat(),
            "orientation": _orientation(doc), "hints": hints}


def build_prompt(ctx: dict[str, Any]) -> str:
    kinds = ('Tipos: "official" = material oficial (arte, captura, foto de imprensa); "news" = a imagem principal da '
             'notícia (page_url é a matéria); "photo" = foto real do lugar/objeto/pessoa.')
    return (
        f"Vídeo: {ctx['title']}" + (f" — {ctx['topic']}" if ctx.get("topic") else "") + "\n"
        f"Hoje: {ctx['today']}. Orientação do vídeo: {ctx['orientation']}.\n"
        f"{kinds}\nAté {ASK_PER_SCENE} candidatos por cena. Cenas:\n"
        + json.dumps(ctx["hints"], ensure_ascii=False, indent=1)
    )


def _ask_llm(ctx: dict[str, Any], project_id: str | None, job_id: str | None) -> LLMResult:
    """Só o Claude Code (com as ferramentas web): outro modelo da cadeia responderia sem buscar, com link inventado."""
    prompt = build_prompt(ctx)

    def call(provider):
        return provider.complete_json(task=TASK, system=SYSTEM, prompt=prompt, schema=SCHEMA,
                                      max_tokens=8000, context=ctx)

    try:
        result, _provider, _ = registry.run_chain(
            "llm", [WEB_PROVIDER], call,
            usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
            project_id=project_id, job_id=job_id,
        )
    except ProviderError as exc:
        raise MediaFindError(f"A busca de arte oficial e notícias na web falhou: {exc}") from exc
    return result


def parse_candidates(data: object, scene_ids: set[str]) -> dict[str, list[dict[str, str]]]:
    """Candidatos por cena (só das cenas pedidas), sem repetir a imagem, com no máximo o dobro do que fica."""
    out: dict[str, list[dict[str, str]]] = {}
    scenes = data.get("scenes") if isinstance(data, dict) else None
    for entry in scenes if isinstance(scenes, list) else []:
        if not isinstance(entry, dict) or entry.get("scene_id") not in scene_ids:
            continue
        bucket = out.setdefault(str(entry["scene_id"]), [])
        seen = {c["image"] for c in bucket}
        for item in entry.get("candidates") or []:
            if not isinstance(item, dict):
                continue
            image = str(item.get("image") or "").strip()
            if not image or image in seen or len(bucket) >= MAX_PER_SCENE * 2:
                continue
            # Ilustração, arte de fã ou de IA: não é material real (regra 2). O título diz quem fez a imagem; o
            # endereço e o "why" não entram (o assunto da matéria e "não é fan art" não dizem nada da autoria).
            if str(item.get("made") or "").strip().lower() == "illustration" or looks_synthetic(item.get("title")):
                continue
            seen.add(image)
            bucket.append({k: str(item.get(k) or "").strip() for k in ("image", "page_url", "title", "outlet", "why")})
    return out


# --------------------------------------------------------------------------------------------
# Conferência dos candidatos (sem LLM)
# --------------------------------------------------------------------------------------------


def _https(url: str) -> str:
    text = (url or "").strip()
    return text if text.lower().startswith("https://") and len(text) <= URL_MAX else ""


def _size(content: bytes) -> tuple[int, int] | None:
    from PIL import Image  # aqui: só a conferência carrega o Pillow

    try:
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
    except Exception:  # noqa: BLE001 - não é imagem que o Pillow lê
        return None
    return (int(width), int(height)) if width > 0 and height > 0 else None


def _probe(url: str, http: httpx.Client, *, follow_page: bool = True) -> tuple[str, int, int] | None:
    """(endereço, largura, altura) da imagem. Se o endereço é uma página, tenta a og:image dela (uma vez)."""
    url = _https(url)
    if not url:
        return None
    try:
        fetched = safe_http.fetch(url, max_bytes=PROBE_BYTES, accept=safe_http.ACCEPT_IMAGE, http=http, truncate=True)
    except safe_http.FetchError:
        return None
    ctype = fetched.content_type
    if ctype.startswith("text/html") or ctype == "application/xhtml+xml":
        if not follow_page:
            return None
        html = fetched.content.decode(fetched.charset or "utf-8", errors="replace")
        image = link_preview.parse_meta(html, fetched.url).image_url
        return _probe(image, http, follow_page=False) if image else None
    if ctype == "image/svg+xml" or not (ctype.startswith("image/") or ctype in ("", "application/octet-stream")):
        return None
    size = _size(fetched.content)
    if size is None:
        return None
    final = _https(fetched.url) or url
    return final, size[0], size[1]


def _from_page(page_url: str, http: httpx.Client) -> tuple[str, int, int] | None:
    """A og:image da página (a imagem principal da notícia ou da página oficial)."""
    page = _https(page_url)
    if not page:
        return None
    try:
        preview = link_preview.read_link(page, http=http)
    except safe_http.FetchError:
        return None
    if preview.kind == "image" and preview.image:
        size = _size(preview.image)
        return (_https(preview.url) or page, *size) if size else None
    return _probe(preview.image_url, http, follow_page=False) if preview.image_url else None


def validate_candidate(item: dict[str, str], kind: str, http: httpx.Client) -> FoundMedia | None:
    """O candidato conferido, ou None (link http, não imagem, pequeno demais ou fora do ar). Nunca levanta erro."""
    try:
        found = _probe(item.get("image", ""), http) or _from_page(item.get("page_url", ""), http)
        if found is None:
            return None
        image, width, height = found
        if max(width, height) < MIN_LONG_SIDE:
            return None
        page = _https(item.get("page_url", ""))
        outlet = _clip(item.get("outlet"), 80) or link_preview.site_label(page or image)
        return FoundMedia(image=image, page_url=page, title=_clip(item.get("title"), 200), outlet=outlet,
                          kind=kind, width=width, height=height, why=_clip(item.get("why"), 240))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - um candidato ruim nunca derruba a busca
        log.warning("candidato de mídia descartado (%s): %s", item.get("image"), exc)
        return None


def validate_all(candidates: dict[str, list[dict[str, str]]], kinds: dict[str, str], *,
                 http: httpx.Client | None = None, budget_s: float = VALIDATE_BUDGET_S,
                 workers: int = VALIDATE_WORKERS) -> dict[str, list[FoundMedia]]:
    """Confere os candidatos em paralelo até `budget_s` segundos. Fica a ordem do modelo, sem imagem repetida, até
    `MAX_PER_SCENE` por cena."""
    jobs = [(sid, i, item) for sid, items in candidates.items() for i, item in enumerate(items)]
    results: dict[tuple[str, int], FoundMedia] = {}
    if jobs:
        client = http or safe_http.new_client()
        pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs))))
        try:
            futures = {pool.submit(validate_candidate, item, kinds[sid], client): (sid, i) for sid, i, item in jobs}
            deadline = time.monotonic() + budget_s
            pending = set(futures)
            while pending:
                left = deadline - time.monotonic()
                if left <= 0:
                    log.warning("conferência dos candidatos parou no tempo (%d sem resposta)", len(pending))
                    break
                done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
                for future in done:
                    value = future.result()
                    if value is not None:
                        results[futures[future]] = value
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            if http is None:
                client.close()
    out: dict[str, list[FoundMedia]] = {}
    for sid, items in candidates.items():
        kept: list[FoundMedia] = []
        seen: set[str] = set()
        for i in range(len(items)):
            found = results.get((sid, i))
            if found is None or found.image in seen:
                continue
            seen.add(found.image)
            kept.append(found)
            if len(kept) >= MAX_PER_SCENE:
                break
        out[sid] = kept
    return out


# --------------------------------------------------------------------------------------------
# Gravação e job
# --------------------------------------------------------------------------------------------


def _same_hint(a: RealHint, b: RealHint) -> bool:
    return (a.kind, a.note, a.query, a.source_ref) == (b.kind, b.note, b.query, b.source_ref)


def save_found(project_id: str, asked: dict[str, RealHint], found: dict[str, list[FoundMedia]]) -> int | None:
    """Relê o documento e grava os candidatos só nas cenas cuja sugestão não mudou durante a busca (e continuam sem
    mídia). Devolve a versão nova (None: nada a gravar)."""
    doc = service.get_doc(project_id)
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    changed = 0
    for scene in doc.scenes:
        before = asked.get(scene.id)
        hint = scene.real
        if before is None or hint is None or scene.visual.media is not None or not _same_hint(before, hint):
            continue
        hint.found = list(found.get(scene.id, []))
        hint.searched = True
        hint.searched_at = now
        changed += 1
    if not changed:
        return None
    kept = sum(len(v) for v in found.values())
    message = f"Busca de mídia real na web: {kept} candidatos para {changed} cenas"
    return service.commit(project_id, ProjectDoc.model_validate(doc.model_dump()), message, author="ai")


def run_media_find(project_id: str, *, refresh: bool = False, job_id: str | None = None,
                   http: httpx.Client | None = None, progress: Callable[[float, str], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Busca os candidatos das sugestões pendentes numa chamada só. Resultado: `{scenes, found, message, ...}`."""
    report = progress or (lambda _f, _m: None)
    doc = service.get_doc(project_id)
    scenes = pending_scenes(doc, refresh=refresh)
    if not scenes:
        return {"scenes": 0, "found": 0, "skipped": 0, "message": "Nenhuma sugestão de mídia real para buscar"}
    available, reason = web_search_status()
    if get_settings().offline or not available:
        why = "modo offline" if get_settings().offline else reason
        return {"scenes": 0, "found": 0, "skipped": len(scenes), "unavailable": True,
                "message": f"{UNAVAILABLE_MESSAGE} ({why})."}
    batch, rest = scenes[:MAX_HINTS], scenes[MAX_HINTS:]
    if rest:
        log.info("media_find: %d sugestões ficaram para a próxima busca", len(rest))
    report(0.05, f"Buscando arte oficial e notícias para {len(batch)} cenas")
    ctx = build_context(doc, batch)
    if cancelled is not None and cancelled():
        raise BuildCancelled("busca cancelada")  # antes da chamada paga
    result = _ask_llm(ctx, project_id, job_id)
    if cancelled is not None and cancelled():
        raise BuildCancelled("busca cancelada")
    ids = {s.id for s in batch}
    candidates = parse_candidates(result.data, ids)
    total = sum(len(v) for v in candidates.values())
    report(0.7, f"Conferindo {total} imagens")
    kinds = {s.id: s.real.kind for s in batch if s.real is not None}
    found = validate_all(candidates, kinds, http=http)
    if cancelled is not None and cancelled():
        raise BuildCancelled("busca cancelada")
    asked = {s.id: s.real for s in batch if s.real is not None}
    version = save_found(project_id, asked, found)
    kept = sum(len(v) for v in found.values())
    with_media = sum(1 for v in found.values() if v)
    message = f"{kept} imagens achadas para {with_media} de {len(batch)} cenas"
    if rest:
        message += f" ({len(rest)} cenas ficaram para a próxima busca)"
    report(1.0, message)
    return {"scenes": len(batch), "found": kept, "skipped": len(rest), "checked": total, "version": version,
            "message": message}


# --------------------------------------------------------------------------------------------
# Acervo antes do storyboard (ADR 0004 §3.4): por tópico do roteiro, não por cena
# --------------------------------------------------------------------------------------------

MAX_TOPICS = 15
"""Tópicos por chamada no acervo (a busca por cena tem `MAX_HINTS`)."""


def web_available() -> bool:
    """Online e com o Claude Code buscando na web (nunca levanta erro)."""
    if get_settings().offline:
        return False
    try:
        return bool(web_search_status()[0])
    except Exception as exc:  # noqa: BLE001 - só decide se busca
        log.warning("não deu para saber se há busca web: %s", exc)
        return False


def topics_context(doc: ProjectDoc, topics: list[tuple[str, str, str]], *, today: str | None = None) -> dict[str, Any]:
    """O pedido no mesmo formato do de cenas: cada tópico `(id, rótulo, busca)` entra como uma "cena" (o id do
    tópico vai em `scene_id`)."""
    hints = [{"scene_id": tid, "kind": "official",
              "note": _clip(f"Tópico do vídeo: {label}. Arte oficial, captura de tela, foto de imprensa ou a imagem "
                            f"principal de uma notícia sobre isto.", 300),
              "query": _clip(query, 160), "narration": ""} for tid, label, query in topics[:MAX_TOPICS]]
    return {"title": doc.title, "topic": _clip(doc.topic, 300), "today": today or date.today().isoformat(),
            "orientation": _orientation(doc), "hints": hints}


def find_for_topics(doc: ProjectDoc, topics: list[tuple[str, str, str]], *, project_id: str | None = None,
                    job_id: str | None = None, http: httpx.Client | None = None) -> dict[str, list[FoundMedia]]:
    """Arte oficial e notícias para até `MAX_TOPICS` tópicos numa chamada só (uma por vídeo). Sem busca web
    (offline, outro LLM): `{}` sem chamar nada. Erro da chamada levanta `MediaFindError`."""
    topics = [t for t in topics if t[0] and t[1]][:MAX_TOPICS]
    if not topics or not web_available():
        return {}
    ctx = topics_context(doc, topics)
    result = _ask_llm(ctx, project_id, job_id)
    ids = {t[0] for t in topics}
    candidates = parse_candidates(result.data, ids)
    return validate_all(candidates, {tid: "official" for tid in ids}, http=http)


def should_search(doc: ProjectDoc) -> bool:
    """Vale buscar no fim do storyboard: online, com sugestão oficial/notícia pendente e o Claude Code buscando na
    web. Nunca levanta erro."""
    if get_settings().offline or not has_pending(doc):
        return False
    try:
        return bool(web_search_status()[0])
    except Exception as exc:  # noqa: BLE001 - só decide se busca
        log.warning("não deu para saber se há busca web: %s", exc)
        return False


def queue_find(project_id: str, doc: ProjectDoc, submit: Callable[..., dict[str, Any]]) -> tuple[str | None, str | None]:
    """No fim do storyboard (fora do piloto automático): a busca vira um job `media_find` próprio, para as cenas
    ficarem prontas na hora (o job pode ser cancelado e a edição das cenas não espera). Devolve (id do job, aviso)."""
    if not should_search(doc):
        return None, None
    try:
        return str(submit("media_find", {}, project_id)["id"]), None
    except Exception as exc:  # noqa: BLE001 - nunca derruba o storyboard
        log.warning("a busca de arte oficial e notícias não entrou na fila: %s", exc)
        return None, f"A busca automática de arte oficial e notícias não começou: {exc}"


def auto_find(project_id: str, doc: ProjectDoc, *, job_id: str | None = None,
              progress: Callable[[float, str], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """No piloto automático (dentro do mesmo job): busca só se há sugestão oficial/notícia pendente e o Claude Code
    busca na web. Devolve (resultado, aviso). Nunca levanta erro (fora o cancelamento): a busca é só uma ajuda."""
    if not should_search(doc):
        return None, None
    try:
        return run_media_find(project_id, job_id=job_id, progress=progress, cancelled=cancelled), None
    except BuildCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - nunca derruba o storyboard
        log.warning("a busca de arte oficial e notícias falhou: %s", exc)
        return None, f"A busca automática de arte oficial e notícias falhou: {exc}"
