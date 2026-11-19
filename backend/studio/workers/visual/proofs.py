"""Prova na tela: captura a página com os trechos marcados e põe a imagem (já marcada e recortada) na cena.

Origem: `shot_helper.shoot` + `shot_views.main` do canais. A prova vira uma `MediaRef` comum (o renderer não muda),
anexada pelo mesmo caminho da rota de mídia (`media_edit.attach_media` + `service.commit`).
Arquivos: `uploads/<projeto>/captures/<id>.png` (página) e `.txt` (texto da página, para a checagem literal).
"""

from __future__ import annotations

import io
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from studio.config import get_settings
from studio.core.project import media_edit, service
from studio.core.project.document import MediaRef
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.ids import new_id
from studio.media import page_capture, real_media
from studio.media.blocked_page import blocked_reason
from studio.media.page_capture import BlockedPage, CaptureError, OpenPage
from studio.models import PageCapture

_LOCK = threading.Lock()  # rebake e captura gravam o mesmo documento e a mesma linha


class ProofError(ValueError):
    """Prova recusada (cena travada, sem trecho, captura não encontrada): mensagem em português."""


class BoilerplateOnly(ProofError):
    """A captura da página só mostrou o rodapé ou a caixa de newsletter (`CaptureResult.boilerplate_only`): nem a
    captura antiga do cache serve no lugar (ela é do mesmo jeito)."""


class BlockedCapture(ProofError):
    """A página da fonte era a verificação anti-robô, o captcha, o bloqueio ou o erro do site (`BlockedPage`), e não
    há captura boa guardada: a fonte fica sem captura (a cena usa o cartão do dado com a fonte)."""


BLOCKED_KIND = "bloqueio"
"""`rejected` do cache da captura (`pool-<chave>.json`) da página que só mostrou o bloqueio."""


def _text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def stored_block(title: str | None, text_path: Path | None, expected: Sequence[str] = ()) -> str | None:
    """A captura gravada é de uma página de bloqueio (título e texto guardados, `blocked_page.blocked_reason`)?
    Sem o texto guardado, só o título conta. Página sem texto não conta aqui: as antigas não guardavam o texto."""
    text = _text_file(text_path) if text_path is not None else ""
    return blocked_reason(title or "", text, expected=expected)


def capture_dict(row: PageCapture) -> dict[str, Any]:
    return {
        "id": row.id, "project_id": row.project_id, "scene_id": row.scene_id, "url": row.url, "domain": row.domain,
        "title": row.title, "image": row.image, "proof": row.proof, "width": row.width, "height": row.height,
        "dark": bool(row.dark), "phrases": row.phrases or [], "text_path": row.text_path, "status": row.status,
        "error": row.error, "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _aspect(project_id: str) -> float:
    doc = service.get_doc(project_id)
    fmt = get_style(doc.style_id, doc.style_overrides).format
    return fmt.width / fmt.height if fmt.height else 16 / 9


def _focus_index(phrases: list[dict[str, Any]]) -> int | None:
    """O trecho que manda no recorte: o marcado como foco, se tiver janela; senão o primeiro com janela."""
    marked = next((i for i, p in enumerate(phrases) if p.get("focus") and p.get("view")), None)
    if marked is not None:
        return marked
    return next((i for i, p in enumerate(phrases) if p.get("view")), None)


def compute_views(img: Image.Image, phrases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Janela de cada trecho visível (como `shot_views.main`); trecho travado mantém a janela ajustada à mão."""
    W, H = img.size
    mask = page_capture.ink_mask(img)
    out = []
    for ph in phrases:
        ph = dict(ph)
        rects = ph.get("rects") or []
        if ph.get("lock") and ph.get("view"):
            out.append(ph)
            continue
        ph["view"] = page_capture.view_for(mask, rects, W, H) if page_capture.visible(rects) else None
        out.append(ph)
    return out


def bake(img: Image.Image, phrases: list[dict[str, Any]], dark: bool, aspect: float) -> Image.Image:
    """A prova: todos os trechos visíveis marcados e o recorte em volta do trecho de foco (página inteira sem
    nenhum trecho visível)."""
    rects = [r for ph in phrases if page_capture.visible(ph.get("rects")) for r in ph["rects"]]
    marked = page_capture.mark(img, rects, dark)
    focus = _focus_index(phrases)
    view = phrases[focus]["view"] if focus is not None else [0.0, 0.0, 1.0, 1.0]
    return page_capture.crop_to_view(marked, view, aspect)


def _store(img: Image.Image, project_id: str) -> real_media.StoredImage:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return real_media.store_image(buf.getvalue(), project_id)


def _attach(project_id: str, scene_id: str, media: MediaRef, message: str) -> int:
    """Mesmo caminho da rota de mídia: documento lido de novo, `attach_media` e `commit`."""
    doc = service.get_doc(project_id)
    try:
        new_doc = media_edit.attach_media(doc, scene_id, media)
    except Exception as exc:  # OpError: cena travada ou sumiu
        raise ProofError(str(exc)) from exc
    return service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: {message}")


def _media(stored: real_media.StoredImage, url: str, title: str, domain: str) -> MediaRef:
    return MediaRef(kind="official", source="link", asset=stored.rel, url="", page_url=url, title=title,
                    outlet=domain, credit=domain, editorial=False, width=stored.width, height=stored.height)


def _record_failure(project_id: str, scene_id: str, url: str, texts: list[str], focus: int, exc: Exception) -> None:
    """Grava a `PageCapture` com `status="falhou"` e a mensagem, para o painel não mostrar a captura anterior como
    se fosse a atual. Uma falha aqui não esconde o erro original."""
    message = str(exc) if isinstance(exc, CaptureError) else f"A captura falhou: {exc}"
    try:
        with session_scope() as s:
            s.add(PageCapture(id=new_id("cap"), project_id=project_id, scene_id=scene_id, url=url,
                              domain=page_capture.domain_of(url), title="", image="", proof="", width=0, height=0,
                              dark=False, status="falhou", error=message[:2000],
                              phrases=[{"text": t, "rects": [], "found": False, "view": None, "lock": False,
                                        "focus": i == focus} for i, t in enumerate(texts)]))
    except Exception:  # noqa: BLE001 - o erro que importa é o da captura
        pass


def capture_proof(project_id: str, scene_id: str, url: str, phrases: Sequence[str], focus: int = 0, *,
                  open_page: OpenPage | None = None,
                  progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Captura, marca, recorta, anexa à cena e grava a `PageCapture`. `status="parcial"` quando um trecho não foi
    achado ou ficou fora da tela."""
    say = progress or (lambda f, m: None)
    texts = [" ".join(p.split()) for p in phrases if p and p.strip()]
    if not 1 <= len(texts) <= page_capture.MAX_PHRASES:
        raise ProofError(f"Escreva de 1 a {page_capture.MAX_PHRASES} trechos, um por linha.")
    doc = service.get_doc(project_id)
    try:
        scene = doc.scene(scene_id)
    except KeyError as exc:
        raise ProofError("Cena não encontrada.") from exc
    if scene.locked:
        raise ProofError("A cena está travada: destrave para mudar a mídia real.")
    focus = focus if 0 <= focus < len(texts) else 0
    say(0.05, "Abrindo a página")
    try:
        shot = page_capture.capture(url, texts, focus, open_page=open_page)
    except Exception as exc:
        _record_failure(project_id, scene_id, url, texts, focus, exc)
        raise
    say(0.6, "Marcando os trechos")
    cap_id = new_id("cap")
    data_dir = get_settings().data_dir
    folder = data_dir / "uploads" / project_id / "captures"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{cap_id}.png").write_bytes(shot.png)
    (folder / f"{cap_id}.txt").write_text(shot.text, encoding="utf-8")
    with Image.open(io.BytesIO(shot.png)) as im:
        img = im.convert("RGB")
    dark = page_capture.is_dark(img)
    raw = [{"text": t, "rects": shot.rects[i] or [], "found": bool(shot.found[i]) or bool(shot.rects[i]),
            "view": None, "lock": False, "focus": i == focus} for i, t in enumerate(texts)]
    phrases_out = compute_views(img, raw)
    partial = any(not p.get("view") for p in phrases_out)
    proof = bake(img, phrases_out, dark, _aspect(project_id))
    say(0.8, "Guardando a prova")
    with _LOCK:
        stored = _store(proof, project_id)
        media = _media(stored, shot.url, shot.title, shot.domain)
        version = _attach(project_id, scene_id, media, f"prova na tela anexada ({shot.domain})")
        with session_scope() as s:
            row = PageCapture(id=cap_id, project_id=project_id, scene_id=scene_id, url=shot.url, domain=shot.domain,
                              title=shot.title, image=f"uploads/{project_id}/captures/{cap_id}.png", proof=stored.rel,
                              width=img.width, height=img.height, dark=dark, phrases=phrases_out,
                              text_path=f"uploads/{project_id}/captures/{cap_id}.txt",
                              status="parcial" if partial else "ok",
                              error="Trecho não achado ou fora da tela: confira o texto ou ajuste o enquadramento."
                              if partial else None)
            s.add(row)
            s.flush()
            out = capture_dict(row)
    say(1.0, "Prova pronta")
    return {**out, "version": version, "warnings": stored.warnings}


# --------------------------------------------------------------------------------------------
# Captura limpa para o acervo (ADR 0004 §3.4): sem marca-texto queimado, com os retângulos dos trechos
# --------------------------------------------------------------------------------------------


def clean_proof(img: Image.Image, phrases: list[dict[str, Any]], aspect: float) -> tuple[Image.Image, list[list[float]]]:
    """O mesmo recorte da prova (`bake`), mas sem o marca-texto: a imagem limpa e os retângulos de todos os trechos
    visíveis no quadro recortado (o renderer anima o destaque)."""
    rects = [r for ph in phrases if page_capture.visible(ph.get("rects")) for r in ph["rects"]]
    focus = _focus_index(phrases)
    view = phrases[focus]["view"] if focus is not None else [0.0, 0.0, 1.0, 1.0]
    return page_capture.clean_crop(img, view, rects, aspect)


def _clean_entry(img: Image.Image, phrases: list[dict[str, Any]], project_id: str, aspect: float,
                 **meta: Any) -> dict[str, Any]:
    clean, rects = clean_proof(img, phrases, aspect)
    stored = _store(clean, project_id)
    return {"asset": stored.rel, "rects": rects, "width": stored.width, "height": stored.height, **meta}


def clean_from_capture(data: dict[str, Any], aspect: float) -> dict[str, Any] | None:
    """A captura gravada (`capture_dict`) em versão limpa para o acervo: `{asset, rects, width, height, url, domain,
    title, text}`. None quando a página sumiu ou a captura falhou."""
    if data.get("status") == "falhou" or not data.get("image") or not data.get("project_id"):
        return None
    data_dir = get_settings().data_dir
    path = data_dir / data["image"]
    if not path.is_file():
        return None
    phrases = [dict(p) for p in data.get("phrases") or []]
    if stored_block(data.get("title"), data_dir / data["text_path"] if data.get("text_path") else None,
                    [str(p.get("text") or "") for p in phrases]):
        return None  # a prova gravada é a tela de bloqueio do site: nunca entra no acervo
    with Image.open(path) as im:
        img = im.convert("RGB")
    text = next((p.get("text") for p in phrases if p.get("focus")), None) or next(
        (p.get("text") for p in phrases), "")
    return _clean_entry(img, phrases, data["project_id"], aspect, url=data.get("url") or "",
                        domain=data.get("domain") or "", title=data.get("title") or "", text=text or "")


def _pool_capture_texts(phrases: Sequence[str]) -> list[str]:
    return [" ".join(p.split()) for p in phrases if p and p.strip()][: page_capture.MAX_PHRASES]


def _pool_capture_file(project_id: str, url: str, texts: list[str], aspect: float) -> Path:
    import hashlib
    import json

    key = hashlib.sha256(json.dumps([url, texts, round(aspect, 4)], ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return get_settings().data_dir / "uploads" / project_id / "captures" / f"pool-{key}.json"


def cached_capture_clean(project_id: str, url: str, phrases: Sequence[str], aspect: float) -> dict[str, Any] | None:
    """A captura de `capture_clean` já feita (mesmo endereço, trechos e proporção), sem abrir o navegador. None quando
    ainda não foi capturada ou a imagem sumiu. A coleta rápida do storyboard usa isto para não perder as fontes."""
    import json

    texts = _pool_capture_texts(phrases)
    if not texts:
        return None
    done = _pool_capture_file(project_id, url, texts, aspect)
    if not done.is_file():
        return None
    try:
        cached = json.loads(done.read_text(encoding="utf-8"))
        if cached.get("rejected"):
            return None  # a página só mostrou o rodapé: fora do acervo até uma coleta completa conseguir a página
        if stored_block(cached.get("title"), done.with_suffix(".txt"), texts):
            return None  # a página de bloqueio que entrou antes desta checagem: a próxima coleta completa refaz
        path = get_settings().data_dir / cached["asset"]
        if path.is_file():
            with Image.open(path) as im:
                if is_blank_page(im):
                    return None  # captura antiga em branco: a próxima coleta completa tenta de novo
            return cached
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


BLANK_PAGE_STDDEV = 4.0
"""Desvio padrão do cinza abaixo do qual a captura é uma cor só (página que não carregou: site em JS pesado, como o da
Rockstar, sai como o fundo vazio)."""


def is_blank_page(img: Image.Image) -> bool:
    from PIL import ImageStat

    return ImageStat.Stat(img.convert("L")).stddev[0] < BLANK_PAGE_STDDEV


def capture_clean(project_id: str, url: str, phrases: Sequence[str], aspect: float, *,
                  open_page: OpenPage | None = None) -> dict[str, Any]:
    """Captura uma página para o acervo (sem cena e sem marca-texto), com cache pelo endereço e pelos trechos:
    `uploads/<projeto>/captures/pool-<chave>.png|.txt|.json`. Devolve o mesmo formato de `clean_from_capture`."""
    import json

    texts = _pool_capture_texts(phrases)
    if not texts:
        raise ProofError("Escreva pelo menos um trecho para marcar.")
    done = _pool_capture_file(project_id, url, texts, aspect)
    folder, key = done.parent, done.stem.removeprefix("pool-")
    cached = cached_capture_clean(project_id, url, texts, aspect)
    if (cached is not None and (cached.get("rects") or cached.get("headline"))
            and int(cached.get("find") or 1) >= page_capture.FIND_VERSION):
        return cached
    # Sem retângulo, de antes da busca pela manchete ou de uma busca antiga (a 1 marcava o título achado na caixa de
    # newsletter do rodapé): captura de novo (sem o retângulo certo a edição não sabe onde está o texto). Se a nova
    # falhar, a antiga ainda serve melhor que nenhuma prova; a que só mostra o rodapé, não.
    try:
        shot = page_capture.capture(url, texts, 0, open_page=open_page, headline=True)
        late = blocked_reason(shot.title, shot.text, expected=texts)
        if late is not None:
            raise BlockedPage(late, url)
        if shot.boilerplate_only:
            folder.mkdir(parents=True, exist_ok=True)
            done.write_text(json.dumps({"rejected": "rodapé", "find": page_capture.FIND_VERSION}), encoding="utf-8")
            raise BoilerplateOnly("A página só mostrou o rodapé (o conteúdo não carregou): envie um print ou tente de "
                                  "novo.")
        with Image.open(io.BytesIO(shot.png)) as im:
            img = im.convert("RGB")
        if is_blank_page(img):
            raise ProofError("A página saiu em branco (não carregou a tempo): envie um print ou tente de novo.")
    except BoilerplateOnly:
        raise
    except BlockedPage as exc:
        # A tela de bloqueio nunca entra no cache como boa: a captura boa de antes continua (a fonte não mudou); sem
        # ela, o cache guarda a recusa (a coleta rápida não a acha e a próxima completa tenta de novo).
        if cached is not None:
            return cached
        folder.mkdir(parents=True, exist_ok=True)
        done.write_text(json.dumps({"rejected": BLOCKED_KIND, "reason": exc.reason, "url": url,
                                    "find": page_capture.FIND_VERSION}, ensure_ascii=False), encoding="utf-8")
        raise BlockedCapture(str(exc)) from exc
    except Exception:
        if cached is not None:
            return cached
        raise
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"pool-{key}.png").write_bytes(shot.png)
    (folder / f"pool-{key}.txt").write_text(shot.text, encoding="utf-8")
    raw =[{"text": t, "rects": shot.rects[i] or [], "found": bool(shot.found[i]) or bool(shot.rects[i]),
            "view": None, "lock": False, "focus": i == 0} for i, t in enumerate(texts)]
    entry = _clean_entry(img, compute_views(img, raw), project_id, aspect, url=shot.url, domain=shot.domain,
                         title=shot.title, text=texts[0], headline=True)
    entry["find"] = page_capture.FIND_VERSION
    done.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    return entry


def capture_rejection(project_id: str, url: str, phrases: Sequence[str], aspect: float) -> str | None:
    """Por que a fonte não tem captura no cache (`rejected` de `capture_clean`, ou a captura guardada que é a página
    de bloqueio), em português; None quando não foi recusada."""
    import json

    texts = _pool_capture_texts(phrases)
    if not texts:
        return None
    done = _pool_capture_file(project_id, url, texts, aspect)
    try:
        cached = json.loads(done.read_text(encoding="utf-8")) if done.is_file() else None
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict):
        return None
    if cached.get("rejected") == BLOCKED_KIND:
        return str(cached.get("reason") or "o site mostrou uma página de bloqueio")
    if cached.get("rejected"):
        return None
    return stored_block(cached.get("title"), done.with_suffix(".txt"), texts)


def blocked_capture_urls(project_id: str) -> dict[str, str]:
    """Endereço → motivo das páginas cuja captura é (ou foi recusada por ser) a tela de bloqueio do site, pelo cache
    das capturas do acervo e pelas provas gravadas. A mídia de cena que ainda aponta para elas sai (`media_pool`)."""
    import json

    out: dict[str, str] = {}
    folder = get_settings().data_dir / "uploads" / project_id / "captures"
    if folder.is_dir():
        for done in sorted(folder.glob("pool-*.json")):
            try:
                cached = json.loads(done.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(cached, dict) or not cached.get("url"):
                continue
            if cached.get("rejected") == BLOCKED_KIND:
                out[str(cached["url"])] = str(cached.get("reason") or "o site mostrou uma página de bloqueio")
            elif not cached.get("rejected"):
                why = stored_block(cached.get("title"), done.with_suffix(".txt"), [str(cached.get("text") or "")])
                if why:
                    out[str(cached["url"])] = why
    try:
        rows = list_captures(project_id)
    except Exception:  # noqa: BLE001 - sem banco (testes de função pura): só o cache conta
        rows = []
    data_dir = get_settings().data_dir
    for row in rows:
        if row.get("status") == "falhou" or not row.get("url"):
            continue
        why = stored_block(row.get("title"), data_dir / row["text_path"] if row.get("text_path") else None,
                           [str(p.get("text") or "") for p in row.get("phrases") or []])
        if why:
            out.setdefault(str(row["url"]), why)
    return out


def get_capture(capture_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.get(PageCapture, capture_id)
        return capture_dict(row) if row else None


def list_captures(project_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = (s.query(PageCapture).filter_by(project_id=project_id)
                .order_by(PageCapture.created_at.desc()).all())
        return [capture_dict(r) for r in rows]


def _clean_view(view: Sequence[float]) -> list[float]:
    if len(view) != 4:
        raise ProofError("A janela precisa de 4 números (x0, y0, x1, y1).")
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in view)
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        raise ProofError("A janela ficou pequena demais.")
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def rebake(capture_id: str, i: int, view: Sequence[float] | None, lock: bool) -> dict[str, Any]:
    """Ajuste à mão: a janela do trecho `i` (None = automática de novo), `lock` preserva a janela, e o trecho passa a
    mandar no recorte. Refaz a prova e troca a imagem na cena quando ela ainda mostra esta captura."""
    with _LOCK:
        with session_scope() as s:
            row = s.get(PageCapture, capture_id)
            if row is None:
                raise LookupError(capture_id)
            data = capture_dict(row)
        phrases = [dict(p) for p in data["phrases"]]
        if not 0 <= i < len(phrases):
            raise ProofError("Trecho não encontrado.")
        path = get_settings().data_dir / data["image"]
        if not path.is_file():
            raise ProofError("A imagem da página sumiu: capture de novo.")
        with Image.open(path) as im:
            img = im.convert("RGB")
        for k, ph in enumerate(phrases):
            ph["focus"] = k == i
        if view is not None:
            phrases[i]["view"] = _clean_view(view)
            phrases[i]["lock"] = bool(lock)
        else:
            phrases[i]["lock"] = False
            phrases = compute_views(img, phrases)
        if not phrases[i].get("view"):
            raise ProofError("Este trecho não aparece na captura: ajuste a janela à mão.")
        project_id = data["project_id"]
        aspect = _aspect(project_id) if project_id else 16 / 9
        proof = bake(img, phrases, bool(data["dark"]), aspect)
        stored = _store(proof, project_id) if project_id else None
        if project_id and data["scene_id"] and stored is not None:
            doc = service.get_doc(project_id)
            try:
                current = doc.scene(data["scene_id"]).visual.media
            except KeyError:
                current = None
            if current is not None and current.asset == data["proof"] and current.asset != stored.rel:
                media = current.model_copy(update={"asset": stored.rel, "width": stored.width, "height": stored.height})
                _attach(project_id, data["scene_id"], media, "enquadramento da prova ajustado")
        with session_scope() as s:
            row = s.get(PageCapture, capture_id)
            assert row is not None
            row.phrases = phrases
            if stored is not None:
                row.proof = stored.rel
            missing = any(not p.get("view") for p in phrases)
            row.status = "parcial" if missing else "ok"
            row.error = row.error if missing else None
            s.flush()
            return capture_dict(row)


__all__ = ["BlockedCapture", "CaptureError", "ProofError", "blocked_capture_urls", "capture_rejection", "capture_proof",
           "rebake", "get_capture", "list_captures", "capture_dict"]
