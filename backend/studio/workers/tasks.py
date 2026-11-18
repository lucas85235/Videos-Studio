"""Jobs do pipeline: roteiro, storyboard, tradução, metadados, prévia, render final e piloto automático."""

from __future__ import annotations

import logging
from typing import Any

from studio.config import get_settings
from studio.core.glossary import GlossaryEntry, glossary_of
from studio.core.hashing import hash_obj
from studio.core.project import service
from studio.core.project.document import THUMB_WORDS_MAX, Metadata
from studio.core.project.pool import Pool, save_pool
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel, utcnow
from studio.providers import media_search
from studio.workers import novelty
from studio.workers.analytics import lessons as lessons_mod
from studio.workers.jobs import JobContext, get_runner, job
from studio.workers.pipeline import (
    authoring,
    edit_plan,
    media_find,
    media_pool,
    storyboard_check,
    video_clips,
)
from studio.workers.pipeline.build import BuildCancelled, Builder
from studio.workers.pipeline.storyboard_check import Issue

log = logging.getLogger(__name__)


def _channel_info(channel_id: str | None) -> dict[str, Any]:
    """Nome, nicho, persona e identidade visual do canal (vazio sem canal)."""
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return {}
        return {"name": row.name or "", "niche": row.niche or "", "persona": row.persona or "",
                "style_bible": dict(row.style_bible or {})}


def _project_lessons(doc: Any, style: Any) -> list[dict[str, Any]]:
    """Lições aplicadas que valem para o projeto (global, canal, estilo e formato). Nunca derruba o job."""
    try:
        return lessons_mod.applied_lessons(doc.channel_id, doc.style_id, style.format.type)
    except Exception as exc:  # noqa: BLE001 - sem lições o pedido sai como antes
        log.warning("não consegui ler as lições aplicadas: %s", exc)
        return []


def _record_lessons(project_id: str, stage: str, lessons: list[dict[str, Any]], version: int | None) -> None:
    """Grava em `settings["lessons_used"][stage]` quais lições entraram no pedido desta etapa.
    Nunca derruba o job: se a gravação falhar, só o registro se perde."""
    try:
        service.update_settings_key(project_id, "lessons_used", stage,
                                    lessons_mod.usage_record(lessons, version=version))
    except Exception as exc:  # noqa: BLE001 - o roteiro já foi gravado; o registro é só um extra
        log.warning("não consegui registrar as lições usadas (%s): %s", stage, exc)


def channel_glossary(channel_id: str | None) -> list[GlossaryEntry]:
    """Glossário de tradução do canal (vazio sem canal)."""
    if not channel_id:
        return []
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return glossary_of(row.settings) if row is not None else []


def _gates(project_id: str) -> dict[str, Any]:
    return service.get_project_row(project_id)["settings"].get("gates", {})


# Status do projeto parado na conferência das cenas (a lista de projetos mostra "Cenas para conferir").
SCENES_REVIEW_STATUS = "storyboard_review"

# --- Pacote: título e thumb antes do roteiro ------------------------------------------------------------------------
PACKAGE_STATUS = "package_review"
PACKAGE_LONG_SIZE = (1280, 720)
PACKAGE_SHORT_SIZE = (1080, 1920)
DUP_MIN_DISTANCE = 10
"""Distância mínima (dHash de 64 bits) entre as prévias de dois candidatos: abaixo disso, são a mesma thumb."""
STUB_NOTICE = "prévia simplificada: o compositor de thumbs (fase 2) ainda não está disponível"


class PackageLocked(ValueError):
    """O pacote está travado (aprovado): destrave antes de gerar ou trocar candidatos."""


def _style_of(doc: Any) -> Any:
    return get_style(doc.style_id, doc.style_overrides)


def package_gate_on(gates: dict[str, Any], style: Any) -> bool:
    """O portão do pacote vale para este projeto: ligado nos gates (só projeto novo grava a chave) e formato efetivo
    longo (um Short derivado de estilo longo, com override de formato, nunca trava)."""
    return bool(gates.get("package", False)) and style.format.type != "short"


def package_missing(doc: Any) -> bool:
    """O pacote ainda não foi gerado: nenhum candidato automático e sem `generated_at`. O candidato manual semeado
    pela ideia (ou pelo título de trabalho) não conta: o piloto gera os outros ao lado dele."""
    pkg = doc.package
    return pkg.generated_at is None and not any(c.source != "manual" for c in pkg.candidates)


def package_size(style: Any) -> tuple[int, int]:
    return PACKAGE_SHORT_SIZE if style.format.type == "short" else PACKAGE_LONG_SIZE


def layers_hash(candidate: Any) -> str:
    """Hash das camadas + título + texto do candidato: muda quando a thumb precisa de nova prévia."""
    return hash_obj({"layers": candidate.layers.model_dump(mode="json"), "title": candidate.title,
                     "thumb_text": candidate.thumb_text})[:12]


def _dhash(img: Any) -> int:
    """dHash de 64 bits (9×8 em cinza), escrito à mão com Pillow."""
    small = img.convert("L").resize((9, 8))
    px = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


FILL_FIX_CONTRAST = 3.0
"""Contraste (WCAG), medido no render contra o fundo atrás do miolo das letras, abaixo do qual a cor escolhida pelo
gerador é trocada (o contorno escuro segura a leitura, mas a palavra perde o destaque)."""


def _better_fill(layers: Any, text_bg: Any, recent: Any, batch: Any,
                 palette: dict[str, str] | None = None) -> str | None:
    """Outra cor para o texto quando a escolhida mede menos de `FILL_FIX_CONTRAST` contra o fundo de verdade (a
    média atrás do miolo das letras que o compositor mediu) ou tem o matiz dele; None quando ela já basta ou nenhuma
    outra é melhor. As cores são as da paleta do estilo (as que o compositor desenha)."""
    from studio.media import thumb_variety
    from studio.media.thumb_check import contrast_ratio

    if not text_bg:
        return None
    current = layers.text.fill
    now = contrast_ratio(thumb_variety.color_rgb(current, palette), text_bg)
    hue = thumb_variety.same_hue(thumb_variety.color_rgb(current, palette), text_bg)
    if now >= FILL_FIX_CONTRAST and not hue:
        return None
    options = [c for c in thumb_variety.TEXT_COLORS if c != current]
    pick = thumb_variety.pick_fill(text_bg, recent, batch, options=options, palette=palette)
    after = contrast_ratio(thumb_variety.color_rgb(pick, palette), text_bg)
    better = after > now + 0.5 or (hue and after >= min(now, FILL_FIX_CONTRAST)
                                   and not thumb_variety.same_hue(thumb_variety.color_rgb(pick, palette), text_bg))
    return pick if better else None


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _stub_render(layers: Any, size: tuple[int, int], palette: dict[str, str]) -> Any:
    """Prévia de reserva enquanto o compositor por camadas não existe: degradê da paleta + o texto da thumb."""
    from PIL import Image, ImageDraw

    from studio.media.thumbnail import load_font

    w, h = size
    top, bottom = _rgb(palette.get("pink", "#FF2E88")), _rgb(palette.get("orange", "#FF8A1F"))
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    words = (layers.text.words if layers.text else "") or ""
    if words:
        font = load_font("Anton", max(24, int(h * (0.22 if w > h else 0.1))))
        x, y = int(w * 0.06), int(h * (0.12 if w > h else 0.4))
        draw.text((x, y), words, font=font, fill=_rgb(palette.get("yellow", "#FFD400")),
                  stroke_width=max(2, int(h * 0.015)), stroke_fill=_rgb(palette.get("ink", "#1A0B2E")))
    return img


def _compose(layers: Any, size: tuple[int, int], *, resolver: Any, palette: dict[str, str],
             logo_asset: str | None = None) -> tuple[Any, list[str]]:
    """Renderiza as camadas com o compositor por camadas (`media/thumb_compose.py`) e o resolvedor de imagens do
    projeto (`thumbs.resolver_for`: biblioteca do canal, poses do avatar, mídia do projeto, material oficial só com o
    canal liberando). Os avisos do compositor (ativo que faltou, material bloqueado) voltam na lista. Se o compositor
    falhar, sai a prévia de reserva com um aviso: nunca derruba o job."""
    try:
        from studio.media import thumb_compose
    except ImportError:  # pragma: no cover - compositor ausente
        return _stub_render(layers, size, palette), [STUB_NOTICE]
    try:
        img = thumb_compose.render(layers, size, resolver, palette, logo_asset=logo_asset)
    except Exception as exc:  # noqa: BLE001 - a prévia de reserva segura a tela; o aviso conta o que houve
        log.warning("o compositor de thumbs falhou: %s", exc)
        return _stub_render(layers, size, palette), [f"o compositor falhou ({exc}); prévia simplificada"]
    notes = [str(w) for w in (img.info.get("layout") or {}).get("warnings") or []]
    return img, list(dict.fromkeys(notes))


def _check(img: Any, candidate: Any, is_long: bool) -> dict[str, Any]:
    """Checagem de legibilidade (`media/thumb_check.py`, com o título do candidato para o `repeat_title`); sempre soma
    as métricas que o pacote mede sozinho (palavras e repetição do título). Nunca bloqueia."""
    score: dict[str, Any] = {}
    try:
        from studio.media import thumb_check

        # Título gerado: avisa acima do alvo (o manual vai até 90 sem aviso).
        title_max = authoring.PACKAGE_TITLE_TARGET if candidate.source == "auto" else None
        score = dict(thumb_check.check(img, candidate.layers, is_long, title=candidate.title, title_max=title_max)
                     or {})
        score.pop("dhash", None)  # o dup do pacote é medido aqui (contra os outros candidatos), não no check
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - checagem nunca bloqueia
        score = {"warnings": [f"a checagem de legibilidade falhou: {exc}"]}
    warnings = [str(w) for w in score.get("warnings") or []]
    words = len(candidate.thumb_text.split())
    score.setdefault("words", words)
    if words > THUMB_WORDS_MAX and not any("palavra" in w for w in warnings):
        warnings.append(f"texto com {words} palavras (máximo {THUMB_WORDS_MAX})")
    layers = candidate.layers
    total = authoring.thumb_visible_words(candidate.thumb_text, layers.pill, list(layers.cards))
    score.setdefault("words_total", total)
    if total > authoring.THUMB_TOTAL_WORDS_MAX and not any("palavra" in w for w in warnings):
        warnings.append(f"texto total visível com {total} palavras (máximo {authoring.THUMB_TOTAL_WORDS_MAX})")
    if (candidate.source == "auto" and len(candidate.title) > authoring.PACKAGE_TITLE_TARGET
            and not any(w.startswith("título com") for w in warnings)):
        warnings.append(f"título com {len(candidate.title)} caracteres: o alvo é até {authoring.PACKAGE_TITLE_TARGET}")
    _, removed = authoring._title_overlap(candidate.thumb_text, candidate.title)
    score.setdefault("repeat_title", len(removed) + 1 if removed else 0)
    if removed:
        warnings.append("o texto da thumb repete o título")
    score["warnings"] = warnings
    return score


def _swap_frame(layers: Any, frames: list[dict[str, Any]], index: int, cost: Any = None,
                avoid_keyart: bool = False) -> tuple[Any, str] | None:
    """As camadas com outro quadro de fundo: o do mesmo assunto (tags fora as genéricas como "claro"; o menos usado,
    com `cost`) ou, sem assunto em comum, o próximo. Atrás de um personagem recortado ou do avatar não entra quadro
    de personagem (gente atrás de gente); a capa repetida dá lugar a um quadro que não é capa (as capas têm a mesma arte), e com
    `avoid_keyart` (um recorte da keyart está no pacote) nenhuma capa entra. Foco e zoom do quadro novo, como na
    geração. None quando não há outro quadro."""
    ref_id = (layers.background.ref or "").removeprefix("frame:")  # id puro ou "frame:<id>"
    current = next((f for f in frames if str(f["id"]) == ref_id), None)
    tags = authoring._subject_tags(current)
    others = [f for f in frames if str(f["id"]) != ref_id]
    if avoid_keyart or authoring._is_keyart(current):
        others = [f for f in others if not authoring._is_keyart(f)] or others
    if layers.presenter is not None or any(not c.asset.startswith(("logo:", "object:")) for c in layers.characters):
        others = [f for f in others if not {"personagem", "keyart"} & set(f.get("tags") or [])] or others
    options = [f for f in others if tags & authoring._subject_tags(f)] or others
    if not options:
        return None
    swap = min(options, key=lambda f: cost(str(f["id"]))) if cost else options[index % len(options)]
    layers = layers.model_copy(deep=True)
    fresh = authoring._frame_layer(swap, layers.template)
    layers.background.ref = str(swap["id"])
    layers.background.t = fresh.get("t")
    layers.background.focus_x = float(fresh["focus_x"])
    layers.background.zoom = float(fresh["zoom"])
    return layers, str(swap["id"])


def _swap_figure(layers: Any, cutouts: list[dict[str, Any]], cost: Any) -> tuple[Any, str] | None:
    """As camadas com outro recorte no lugar do 1º personagem: o do mesmo tipo (`authoring._same_kind`: mesmo
    personagem, solo com solo, rosto com rosto; nunca corpo inteiro no heroi-palavra) menos usado. None sem outro."""
    by_id = {str(c["id"]): c for c in cutouts}
    idx = next((i for i, c in enumerate(layers.characters) if c.asset in by_id), None)
    if idx is None:
        return None
    cur = layers.characters[idx].asset
    taken = {c.asset for c in layers.characters}
    alts = [str(c["id"]) for c in cutouts if str(c["id"]) not in taken
            and authoring._same_kind(by_id[cur], c, layers.template)]
    if not alts:
        return None
    pick = min(alts, key=cost)
    out = layers.model_copy(deep=True)
    out.characters[idx].asset = pick
    return out, pick


def _fix_low_figures(layers: Any, cutouts: list[dict[str, Any]], low: dict[str, dict[str, Any]],
                     cost: Any, frames: list[dict[str, Any]] | None = None,
                     bg_cost: Any = None) -> tuple[Any, list[str]]:
    """As camadas sem recorte que não serve para thumb (`PackageAssets.low_cutouts`: buraco no corpo, corte sem
    encaixe): cada um troca pelo melhor recorte bom do mesmo personagem (`authoring.replace_low_cutout`); sem
    alternativa, a figura sai, e o heroi-palavra que fica sem figura nenhuma (nem avatar) troca o quadro de fundo pelo
    de rosto menos usado (`bg_cost`), como o gerador faz na thumb sem personagem: o quadro escolhido para ficar atrás
    da figura é só um lugar. Devolve (camadas, avisos CORRIGIDO); as mesmas camadas quando não há o que trocar."""
    def key(asset: str) -> str:
        return asset.removeprefix("cutout:")

    if not any(key(c.asset) in low for c in layers.characters):
        return layers, []
    out = layers.model_copy(deep=True)
    taken = {key(c.asset) for c in out.characters}
    keep, notes = [], []
    for c in out.characters:
        bad = low.get(key(c.asset))
        if bad is None:
            keep.append(c)
            continue
        why = str(bad.get("defect") or "qualidade baixa")
        pick = authoring.replace_low_cutout(bad, cutouts, out.template, cost, taken)
        if pick:
            notes.append(f"CORRIGIDO: recorte {key(c.asset)} não serve para thumb ({why}); usei {pick}")
            taken.add(pick)
            c.asset = pick
            keep.append(c)
        else:
            notes.append(f"CORRIGIDO: recorte {key(c.asset)} não serve para thumb ({why}) e não há outro do "
                         "personagem; a figura saiu")
    out.characters = keep
    bg = out.background
    if (not keep and out.presenter is None and out.template == "heroi-palavra" and frames and bg.kind == "frame"):
        ref_id = (bg.ref or "").removeprefix("frame:")
        current = next((f for f in frames if str(f["id"]) == ref_id), None)
        faces = [f for f in frames if f.get("face_box")]
        if faces and not (current or {}).get("face_box"):
            pick = min(faces, key=lambda f: bg_cost(str(f["id"])) if bg_cost else 0.0)
            fresh = authoring._frame_layer(pick, out.template)
            bg.ref, bg.t = str(pick["id"]), fresh.get("t")
            bg.focus_x, bg.zoom = float(fresh["focus_x"]), float(fresh["zoom"])
            notes.append(f"CORRIGIDO: sem a figura, o fundo virou {pick['id']} (quadro com rosto)")
    return out, notes


def _swap_pose(layers: Any, poses: list[dict[str, Any]], cost: Any) -> tuple[Any, str] | None:
    """As camadas com outra pose do avatar: a menos usada, de preferência com a mesma emoção. None sem outra."""
    if layers.presenter is None or not poses:
        return None
    cur = layers.presenter.pose
    emotions = {str(e).lower() for p in poses if p["id"] == cur for e in p.get("emotions") or []}
    alts = [p for p in poses if p["id"] != cur]
    if not alts:
        return None
    same = [p for p in alts if emotions & {str(e).lower() for e in p.get("emotions") or []}]
    pick = min(same or alts, key=lambda p: cost(p["id"]))
    out = layers.model_copy(deep=True)
    out.presenter.pose = pick["id"]
    facing = str(pick.get("facing") or "center")
    out.presenter.flip = facing == out.presenter.side
    return out, str(pick["id"])


def render_package_thumbs(project_id: str, candidate_ids: list[str] | None = None,
                          progress: Any = None, *, demote: bool = False, history: Any = None) -> dict[str, Any]:
    """Prévia (1280×720 no longo, 1080×1920 no Short) e checagem de cada candidato (ou só de `candidate_ids`). Não
    depende de cenas. Diversidade: se dois candidatos saem iguais (dHash < 10), o segundo troca o fundo pelo próximo
    quadro da biblioteca com a mesma tag e renderiza de novo; espelhar não conta como variedade (é a mesma thumb
    para quem vê). Contra as thumbs recentes do canal (`thumb_history.channel_history`): candidata parecida demais
    (dHash < `RECENT_MIN_DISTANCE`, medido também contra o espelho dela) ou com o mesmo fundo e a mesma pose/palavra
    de uma recente troca, nesta ordem e enquanto continuar parecida, o fundo pelo quadro menos usado do mesmo assunto, a
    figura por outro recorte do mesmo personagem e a pose do avatar; se nada adiantar, ganha o aviso e, com `demote`
    (geração do pacote), vai para o fim da lista. As correções (e a da cor do texto sem contraste) só mexem em
    candidato automático ainda não escolhido nem aprovado, com o pacote destravado: os outros só ganham o aviso.
    Grava `image` e `score` só nos candidatos cujas camadas não mudaram durante o render (a edição feita em
    paralelo vence; ela enfileira a própria prévia)."""
    from PIL import Image

    from studio.media import thumb_variety
    from studio.workers.visual import thumb_history
    from studio.workers.visual import thumbs as visual_thumbs

    doc = service.get_doc(project_id)
    style = _style_of(doc)
    is_long = style.format.type != "short"
    size = package_size(style)
    palette = dict(style.thumbnail.palette)
    resolver = visual_thumbs.resolver_for(doc, style)
    logo = style.thumbnail.logo_asset
    wanted = set(candidate_ids or [])
    targets = [c for c in doc.package.candidates if not wanted or c.id in wanted]
    target_ids = {c.id for c in targets}
    data_dir = get_settings().data_dir
    out_dir = get_settings().outputs_dir / project_id / "package"
    out_dir.mkdir(parents=True, exist_ok=True)
    kit = authoring.thumbkit_manifest(doc.channel_id)
    frames = [i for i in kit if (i.get("kind") or "frame") == "frame"]
    landscape = size[0] >= size[1]
    try:
        assets = authoring.package_assets(doc, style, _channel_info(doc.channel_id).get("style_bible"))
        cutouts, poses = assets.cutouts, assets.poses
        low_cutouts = {str(c["id"]): c for c in assets.low_cutouts}
    except Exception as exc:  # noqa: BLE001 - sem os ativos, só a troca de fundo
        log.warning("pacote: ativos da thumb indisponíveis (%s)", exc)
        cutouts, poses, low_cutouts = [], [], {}
    if history is None:
        history = thumb_history.channel_history(doc.channel_id, project_id, landscape=landscape)
    # o que o pacote inteiro usa (também os candidatos ainda por renderizar: a troca não pode cair no fundo ou na
    # pose de um vizinho); cada candidato sai da conta enquanto é refeito e volta com as camadas finais
    batch = thumb_variety.Usage()
    for other in doc.package.candidates:
        batch.add_layers(other.layers)

    def cost_of(kind: str) -> Any:
        return lambda value: thumb_variety.penalty(kind, value, history.usage, batch)

    cutout_items = {str(c["id"]): c for c in cutouts}

    def recent_of(img: Any, layers: Any) -> tuple[int, Any, str | None]:
        """(distância ao espelho ou à imagem da recente mais parecida, a recente, o motivo da semelhança)."""
        dist, like = history.nearest(thumb_history.image_hashes(img), landscape=landscape)
        if dist < thumb_history.RECENT_MIN_DISTANCE:
            return dist, like, f"dHash {dist}"
        attr = history.like(layers, landscape=landscape)
        if attr is not None:
            return dist, attr[0], attr[1]
        return dist, None, None

    def better(new: tuple[int, Any, str | None], old: tuple[int, Any, str | None]) -> bool:
        return (new[2] is None and old[2] is not None) or (
            (new[2] is None) == (old[2] is None) and new[0] > old[0])

    hashes: dict[str, int] = {}

    def dup_of(img: Any) -> tuple[int, int]:
        """(dHash da prévia, a menor distância a outro candidato do pacote): o espelho de outro candidato também é
        "igual" (é a mesma thumb para quem vê a lista)."""
        from PIL import ImageOps

        digest, mirror = _dhash(img), _dhash(ImageOps.mirror(img))
        return digest, min((min(_hamming(digest, h), _hamming(mirror, h)) for h in hashes.values()), default=64)

    for other in doc.package.candidates:  # as prévias que não vão ser refeitas entram na comparação
        if other.id in target_ids or not other.image:
            continue
        try:
            with Image.open(data_dir / other.image) as im:
                hashes[other.id] = _dhash(im)
        except OSError:
            pass
    results: dict[str, tuple[str, dict[str, Any], Any, str]] = {}
    for index, cand in enumerate(targets):
        if progress:
            progress(index / max(1, len(targets)), f"Thumb {index + 1} de {len(targets)}")
        # só o automático que o dono ainda não pegou para si: manual, escolhido, aprovado ou pacote travado ficam
        # como estão (ganham só o aviso)
        may_fix = (cand.source == "auto" and not cand.approved and cand.id != doc.package.chosen_id
                   and not doc.package.locked)
        layers = cand.layers
        batch.add_layers(layers, -1.0)
        fixed_notes: list[str] = []
        if may_fix and low_cutouts:
            # recorte com buraco no corpo (ou que não encaixa) não fica no automático: o do mesmo personagem
            layers, fixed_notes = _fix_low_figures(layers, cutouts, low_cutouts, cost_of("character"), frames,
                                                   cost_of("background"))
        img, notes = _compose(layers, size, resolver=resolver, palette=palette, logo_asset=logo)
        digest, dup = dup_of(img)
        if dup < DUP_MIN_DISTANCE and may_fix and layers.background.kind == "frame" and frames:
            swapped = _swap_frame(layers, frames, index)
            if swapped:
                layers, swap_id = swapped
                img, notes = _compose(layers, size, resolver=resolver, palette=palette, logo_asset=logo)
                digest, dup = dup_of(img)
                notes = [*notes, f"CORRIGIDO: fundo trocado por {swap_id} (a thumb saía igual a outro candidato)"]
        rec = recent_of(img, layers)
        if rec[2] is not None and may_fix:
            keyart_cut = any(authoring._is_keyart(cutout_items.get(v)) for v, c in
                             batch.counts.get("character", {}).items() if c > 0)
            steps = []
            if layers.background.kind == "frame" and frames:
                steps.append(("fundo", lambda L, i=index, k=keyart_cut: _swap_frame(
                    L, frames, i, cost_of("background"), avoid_keyart=k)))
            if cutouts and any(c.asset in cutout_items for c in layers.characters):
                steps.append(("figura", lambda L: _swap_figure(L, cutouts, cost_of("character"))))
            if poses and layers.presenter is not None:
                steps.append(("pose", lambda L: _swap_pose(L, poses, cost_of("pose"))))
            for what, swap in steps:
                if rec[2] is None:
                    break
                swapped = swap(layers)
                if not swapped:
                    continue
                alt_layers, swap_id = swapped
                alt_img, alt_notes = _compose(alt_layers, size, resolver=resolver, palette=palette, logo_asset=logo)
                alt_rec = recent_of(alt_img, alt_layers)
                alt_digest, alt_dup = dup_of(alt_img)
                if better(alt_rec, rec) and alt_dup >= min(dup, DUP_MIN_DISTANCE):
                    was = f"{thumb_history.describe(rec[1])}, {rec[2]}"
                    layers, img, digest, dup, rec = alt_layers, alt_img, alt_digest, alt_dup, alt_rec
                    notes = [*alt_notes, f"CORRIGIDO: {what} trocado por {swap_id} (a thumb saía parecida com uma "
                                         f"recente do canal: {was})"]
        if may_fix and layers.text and layers.text.words.strip() and layers.text.fill != "vice":
            # cor escolhida pelo gerador que, medida no render, quase some no fundo: troca pela de melhor custo
            text_bg = (img.info.get("layout") or {}).get("text_bg")
            fixed = _better_fill(layers, text_bg, history.usage, batch, palette)
            if fixed is not None:
                alt_layers = layers.model_copy(deep=True)
                alt_layers.text.fill = fixed  # type: ignore[union-attr]
                alt_img, alt_notes = _compose(alt_layers, size, resolver=resolver, palette=palette, logo_asset=logo)
                notes = [*alt_notes, f"CORRIGIDO: cor do texto trocada para {thumb_variety.COLOR_NAMES[fixed]} "
                                     "(contraste com o fundo)"]
                layers, img = alt_layers, alt_img
                digest, dup = dup_of(img)
                rec = recent_of(img, layers)
        recent, like, why = rec
        batch.add_layers(layers)
        hashes[cand.id] = digest
        probe = cand.model_copy(update={"layers": layers})
        score = _check(img, probe, is_long)
        score["dup"] = dup
        if dup < DUP_MIN_DISTANCE:
            score["warnings"].append("thumb quase igual a outro candidato")
        score["recent_dup"] = recent
        if why is not None and like is not None:
            score["recent_like"] = {"project_id": like.get("project_id"), "candidate_id": like.get("candidate_id"),
                                    "image": like.get("image"), "why": why}
            score["warnings"].append(f"parecida demais com uma thumb recente do canal "
                                     f"({thumb_history.describe(like)}; {why}): troque fundo, figura ou pose")
        cand_notes = [str(n) for n in cand.score.get("notes") or []]
        score["warnings"] = list(dict.fromkeys([*score["warnings"], *fixed_notes, *notes, *cand_notes]))
        if cand_notes:
            score["notes"] = cand_notes
        name = f"{cand.id}-{layers_hash(probe)}.jpg"
        for old in out_dir.glob(f"{cand.id}-*.jpg"):
            if old.name != name:
                old.unlink(missing_ok=True)
        img.convert("RGB").save(out_dir / name, "JPEG", quality=90)
        rel = (out_dir / name).relative_to(data_dir).as_posix()
        results[cand.id] = (rel, score, layers, layers_hash(cand))
    current = service.get_doc(project_id)  # pode ter mudado durante o render
    changed = 0
    for cand in current.package.candidates:
        if cand.id not in results:
            continue
        rel, score, layers, seen = results[cand.id]
        if layers_hash(cand) != seen:
            continue
        cand.layers, cand.image, cand.score = layers, rel, score
        changed += 1
    if demote and changed:
        # parecida com uma thumb recente do canal: a candidata automática (não escolhida nem aprovada) vai para o fim
        def similar(c: Any) -> bool:
            return (c.id in results and c.source == "auto" and not c.approved and c.id != current.package.chosen_id
                    and bool((c.score or {}).get("recent_like")))

        current.package.candidates = sorted(current.package.candidates, key=similar)
    version = (service.commit(project_id, current, f"Prévias das thumbs do pacote ({changed})", author="ai")
               if changed else None)
    return {"version": version, "rendered": changed, "candidates": list(results)}


def _planned_publish_date(project_id: str, given: Any = None) -> Any:
    """Data prevista de publicação para o pacote: a do payload (`publish_date`, AAAA-MM-DD) ou a publicação agendada
    mais cedo do projeto; None sem nenhuma (o pacote usa hoje). Nunca derruba o job."""
    from datetime import date

    if given:
        try:
            return date.fromisoformat(str(given)[:10])
        except ValueError:
            log.warning("pacote: publish_date inválida: %r", given)
    try:
        from studio.models import Publication

        with session_scope() as s:
            rows = [r.scheduled_at for r in s.query(Publication).filter(Publication.project_id == project_id)
                    if r.scheduled_at is not None]
        return min(rows).date() if rows else None
    except Exception as exc:  # noqa: BLE001 - data prevista é só uma dica para o prompt
        log.warning("pacote: data prevista indisponível (%s)", exc)
        return None


def generate_package(project_id: str, payload: dict[str, Any], *, job_id: str | None = None,
                     progress: Any = None) -> dict[str, Any]:
    """Gera os candidatos (título + thumb) e as prévias. Payload: `{n_titles?, n_thumbs?, keep_manual=True,
    append=False}`. Sem `append`, os candidatos automáticos antigos saem (ficam os manuais, com `keep_manual`, o
    escolhido e os aprovados). Com o pacote travado, recusa (`PackageLocked`)."""
    doc = service.get_doc(project_id)
    if doc.package.locked:
        raise PackageLocked("o pacote está aprovado: destrave para gerar outros candidatos")
    style = _style_of(doc)
    channel = _channel_info(doc.channel_id)
    lessons = _project_lessons(doc, style)
    references = authoring.package_references(doc.channel_id, doc.topic or doc.title)
    from studio.workers.visual import thumb_history

    history = thumb_history.channel_history(doc.channel_id, project_id, landscape=style.format.type != "short")
    n_titles = int(payload.get("n_titles") or style.thumbnail.title_candidates)
    n_thumbs = int(payload.get("n_thumbs") or style.thumbnail.thumb_candidates)
    keep_manual = payload.get("keep_manual", True)
    if progress:
        progress(0.05, "Pensando títulos e thumbs")
    existing = doc.package.candidates
    kept = existing if payload.get("append") else [
        c for c in existing
        if (keep_manual and c.source == "manual") or c.approved or c.id == doc.package.chosen_id]
    candidates, provider = authoring.package_ideas(
        doc, channel, style, max(1, min(n_titles, 8)), max(1, min(n_thumbs, 4)), references=references,
        lessons=lessons, existing_titles=[c.title for c in kept if c.title], project_id=project_id, job_id=job_id,
        publish_date=_planned_publish_date(project_id, payload.get("publish_date")), history=history.usage,
        kept=kept)
    current = service.get_doc(project_id)  # o documento pode ter mudado enquanto o LLM pensava
    if current.package.locked:
        raise PackageLocked("o pacote foi aprovado enquanto os candidatos eram gerados")
    kept_ids = {c.id for c in kept}
    current.package.candidates = [c for c in current.package.candidates if c.id in kept_ids] + candidates
    current.package.references = [r["id"] for r in references]
    current.package.generated_at = utcnow().isoformat(timespec="seconds")
    current = type(current).model_validate(current.model_dump())
    version = service.commit(project_id, current,
                             f"Pacote: {len(candidates)} candidatos de título e thumb ({provider})", author="ai")
    _record_lessons(project_id, "package", lessons, version)
    sub = (lambda f, m="": progress(0.5 + 0.5 * f, m)) if progress else None
    if progress:
        progress(0.5, "Renderizando as thumbs")
    thumbs = render_package_thumbs(project_id, [c.id for c in candidates] + [c.id for c in kept if not c.image],
                                   progress=sub, demote=True, history=history)
    return {"version": thumbs["version"] or version, "provider": provider, "candidates": len(candidates),
            "kept": len(kept), "references": len(references)}


def approve_package(project_id: str, chosen_id: str | None = None, approved_ids: list[str] | None = None) -> int:
    """Trava o pacote no candidato escolhido: o título do projeto, o título dos metadados e o texto da thumb do idioma
    principal passam a ser os dele. `approved_ids` (opcional) troca os aprovados do A/B. Devolve a versão gravada."""
    doc = service.get_doc(project_id)
    if chosen_id is not None:
        if doc.package.candidate(chosen_id) is None:
            raise ValueError(f"candidato não encontrado: {chosen_id}")
        doc.package.chosen_id = chosen_id
    chosen = doc.chosen_package()
    if chosen is None:
        raise ValueError("escolha um candidato antes de aprovar")
    if not chosen.title.strip():
        raise ValueError("o candidato escolhido não tem título")
    if approved_ids is not None:
        wanted = set(approved_ids)
        for cand in doc.package.candidates:
            cand.approved = cand.id in wanted
    chosen.approved = True
    doc.package.locked = True
    doc.title = chosen.title[:200]
    lang = doc.primary_language
    meta = doc.metadata.get(lang) or Metadata()
    before = (meta.title, doc.thumbnail.text.get(lang, ""))
    if before != (chosen.title[:100], chosen.thumb_text[:60] if chosen.thumb_text else before[1]):
        # Outro par aprovado: o título e o texto da thumb dos outros idiomas eram do par antigo. Saem, e a próxima
        # tradução (ou os metadados do idioma) parte do candidato novo.
        for other in [lg for lg in {*doc.languages, *doc.metadata, *doc.thumbnail.text} if lg != lang]:
            doc.thumbnail.text.pop(other, None)
            if other in doc.metadata:
                doc.metadata[other].title = ""
    meta.title = chosen.title[:100]
    doc.metadata[lang] = meta
    if chosen.thumb_text:
        doc.thumbnail.text[lang] = chosen.thumb_text[:60]
    return service.commit(project_id, doc, f"Pacote aprovado: {chosen.title}")


@job("package")
def run_package(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Pacote (título e thumb): candidatos + prévias. Projeto ainda sem roteiro passa para `package_review`."""
    project_id = ctx.project_id
    assert project_id
    result = generate_package(project_id, payload, job_id=ctx.job_id, progress=ctx.progress)
    doc = service.get_doc(project_id)
    if not doc.segments and service.get_project_row(project_id)["status"] in ("draft", PACKAGE_STATUS):
        service.set_status(project_id, PACKAGE_STATUS)
    elif doc.segments:
        result["warnings"] = ["O roteiro foi escrito antes do pacote: o gancho pode não cumprir a promessa da thumb."]
    ctx.progress(1.0, "Títulos e thumbs prontos para escolher")
    return result


@job("package_thumbs")
def run_package_thumbs(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prévia e checagem das thumbs do pacote (todas ou `candidate_ids`). Não depende de cenas."""
    project_id = ctx.project_id
    assert project_id
    result = render_package_thumbs(project_id, payload.get("candidate_ids") or None, progress=ctx.progress)
    ctx.progress(1.0, "Thumbs atualizadas")
    return result


def _auto_package(ctx: JobContext, payload: dict[str, Any]) -> list[str]:
    """Short e série (portão do pacote gravado desligado na criação): gera o pacote sem parar e trava o 1º candidato,
    para o roteiro já sair com o título e a promessa. Falha vira aviso (o roteiro sai como antes)."""
    project_id = ctx.project_id
    assert project_id
    try:
        generate_package(project_id, {k: v for k, v in payload.items() if k in ("n_titles", "n_thumbs")},
                         job_id=ctx.job_id, progress=ctx.progress)
        doc = service.get_doc(project_id)
        if doc.package.candidates:
            approve_package(project_id, doc.package.candidates[0].id)
        return []
    except Exception as exc:  # noqa: BLE001 - sem pacote, o roteiro sai como antes
        log.warning("o pacote automático falhou: %s", exc)
        return [f"O pacote de título e thumb não saiu: {exc}"]


@job("script")
def run_script(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.1, "Escrevendo o roteiro")
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    lessons = _project_lessons(doc, style)
    new_doc, provider = authoring.generate_script(doc, style, project_id=project_id, job_id=ctx.job_id,
                                                  persona=_channel_info(doc.channel_id).get("persona", ""),
                                                  lessons=lessons)
    version = service.commit(project_id, new_doc, f"Roteiro gerado ({provider})", author="ai")
    _record_lessons(project_id, "script", lessons, version)
    service.set_status(project_id, "script_review")
    ctx.progress(1.0, "Roteiro pronto para revisão")
    return {"version": version, "provider": provider, "segments": len(new_doc.segments)}


@job("storyboard")
def run_storyboard(ctx: JobContext, payload: dict[str, Any], *, find_inline: bool = False) -> dict[str, Any]:
    """`find_inline`: a busca de arte oficial e notícias roda aqui mesmo (piloto automático); senão vira um job
    `media_find` próprio na fila."""
    project_id = ctx.project_id
    assert project_id
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    ctx.progress(0.05, "Juntando material real")
    pool, edit_issues = _gather_pool(ctx, project_id, doc, style)
    ctx.progress(0.1, "Planejando as cenas")
    new_doc, provider, review = authoring.storyboard(
        doc, style, project_id=project_id, job_id=ctx.job_id,
        channel=_channel_info(doc.channel_id), styles=authoring.style_catalog(style), pool=pool,
    )
    new_doc, plan_issues = _attach_and_plan(new_doc, style, pool)
    edit_issues += plan_issues
    # A revisão (contagens e achados) sai do documento FINAL, depois do anexo do acervo e do planejador.
    review = _final_review(new_doc, review, edit_issues, style=style, pool=pool)
    # e o aviso de cada cena cuja mídia do acervo ficou pendente (`midia_pendente`)
    review = authoring.review_attached(new_doc, style, review, pool)
    counts = review["counts"]
    message = f"Cenas planejadas ({provider}): {counts['scenes']} cenas, {counts['new_images']} imagens novas"
    version = service.commit(project_id, new_doc, message, author="ai")
    service.update_settings(project_id, {"storyboard_review": {**review, "provider": provider, "version": version}})
    # Com a parada das cenas ligada, as cenas novas esperam a conferência (storyboard() zera a aprovação).
    service.set_status(project_id, SCENES_REVIEW_STATUS if _gates(project_id).get("storyboard", False) else "storyboard")
    _prefetch_media(ctx, new_doc, style)
    result = {"version": version, "provider": provider, "scenes": counts["scenes"], "new_images": counts["new_images"],
              "reused": counts["reused"], "cards": counts["cards"]}
    if counts.get("real_hints"):
        result["real_hints"] = counts["real_hints"]
    warning: str | None = None
    if ctx.cancelled():
        pass  # cancelado: a busca na web (paga) não começa
    elif pool.searched:
        pass  # a coleta do acervo já rodou o `media_find` (pago): não roda duas vezes
    elif find_inline:
        found, warning = _find_media(ctx, new_doc)
        if found:
            result["media_find"] = found
    else:
        find_job, warning = media_find.queue_find(project_id, new_doc, get_runner().submit)
        if find_job:
            result["media_find_job"] = find_job
    ctx.progress(1.0, "Cenas prontas para revisão")
    warnings = [*(i.message for i in edit_issues if i.severity != storyboard_check.CORRIGIDO),
                *([warning] if warning else [])]
    if warnings:
        result["warnings"] = warnings
    return result


POOL_FAILED = "O acervo de material real não saiu nesta rodada: o storyboard seguiu sem ele (o detalhe está no log)."
ATTACH_FAILED = "A mídia escolhida do acervo não foi anexada: as cenas ficaram como o storyboard deixou."
PLAN_FAILED = "O planejador de edição falhou: as cenas ficaram com um plano só, sem eventos."


def _gather_pool(ctx: JobContext, project_id: str, doc: Any, style: Any) -> tuple[Pool, list[Issue]]:
    """Acervo de material real ANTES do storyboard (ADR 0004). Uma fonte só: o resultado da coleta é SEMPRE gravado
    em `pool.json` (vazio também, com `gathered_at`) e é o mesmo `Pool` que o job passa ao storyboard e ao anexo, então
    nenhum acervo velho de uma rodada anterior sobra no disco. Falha vira um acervo vazio com aviso (o detalhe vai para
    o log): o storyboard sai como antes. Devolve o acervo e os avisos como achados da revisão."""
    try:
        pool = media_pool.gather(doc, style, progress=lambda f, m: ctx.progress(0.05 + 0.05 * min(1.0, max(0.0, f)), m),
                                 cancelled=ctx.cancelled)
    except Exception:  # noqa: BLE001 - sem acervo, o storyboard segue com IA e cartões
        log.warning("a coleta do acervo falhou", exc_info=True)
        pool = Pool(warnings=[POOL_FAILED])
    if pool.gathered_at is None:
        pool = pool.model_copy(update={"gathered_at": utcnow().isoformat(timespec="seconds")})
    try:
        save_pool(project_id, pool)
    except OSError:
        log.warning("não deu para gravar o acervo do projeto %s", project_id, exc_info=True)
    return pool, [Issue("acervo_aviso", storyboard_check.AVISO, w) for w in pool.warnings]


def _as_issues(found: Any) -> list[Issue]:
    """Os achados de um encaixe (`Issue`; texto solto vira aviso sem cena)."""
    return [i if isinstance(i, Issue) else Issue("edicao_aviso", storyboard_check.AVISO, str(i)) for i in found or []]


def _attach_and_plan(doc: Any, style: Any, pool: Pool) -> tuple[Any, list[Issue]]:
    """Depois do storyboard e antes de gravar: anexa a mídia escolhida do acervo e planeja subplanos e eventos. Cada
    passo roda numa CÓPIA do documento e só vale se terminar sem exceção e o resultado validar; senão, o documento
    fica como estava antes do passo (nada aplicado pela metade) e sai um aviso em português (o detalhe vai para o log).
    Devolve o documento final e os achados."""
    issues: list[Issue] = []
    steps = (("anexo do acervo", ATTACH_FAILED, lambda d: media_pool.attach_chosen(d, pool)),
             ("planejador de edição", PLAN_FAILED, lambda d: edit_plan.plan(d, style, pool)))
    for name, failed, step in steps:
        work = doc.model_copy(deep=True)
        try:
            found = _as_issues(step(work))
            work = type(doc).model_validate(work.model_dump())
        except Exception:  # noqa: BLE001 - o storyboard continua valendo sem este passo
            log.warning("o %s falhou", name, exc_info=True)
            issues.append(Issue("edicao_falhou", storyboard_check.AVISO, failed))
            continue
        doc = work
        issues += found
    return doc, issues


def _final_review(doc: Any, review: dict[str, Any], extra: list[Issue], *, style: Any = None,
                  pool: Pool | None = None) -> dict[str, Any]:
    """A revisão do storyboard recalculada sobre o documento final: as contagens (`review_counts`) e os achados do
    storyboard mais os do acervo e do planejador, ordenados por gravidade."""
    order = {storyboard_check.GRAVE: 0, storyboard_check.AVISO: 1, storyboard_check.CORRIGIDO: 2}
    issues = [*review.get("issues", []), *(i.as_dict() for i in extra)]
    counts = storyboard_check.review_counts(doc, int(review.get("counts", {}).get("budget", 0)), style=style, pool=pool)
    return {**review, "counts": counts, "issues": sorted(issues, key=lambda i: order.get(i.get("severity"), 9))}


def _find_media(ctx: JobContext, doc: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Busca na web a arte oficial e as notícias das sugestões (uma chamada do Claude Code para o vídeo inteiro), só
    online e com busca web. Falha vira aviso, nunca derruba o storyboard."""
    assert ctx.project_id
    return media_find.auto_find(ctx.project_id, doc, job_id=ctx.job_id,
                                progress=lambda f, m: ctx.progress(0.92 + 0.07 * f, m), cancelled=ctx.cancelled)


@job("media_find")
def run_media_find(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Acha na web arte oficial e imagens de notícia para as sugestões de mídia real pendentes. Payload:
    `{refresh?}` (busca de novo as sugestões já buscadas)."""
    assert ctx.project_id
    return media_find.run_media_find(ctx.project_id, refresh=bool(payload.get("refresh")), job_id=ctx.job_id,
                                     progress=ctx.progress, cancelled=ctx.cancelled)


def _prefetch_media(ctx: JobContext, doc: Any, style: Any) -> None:
    """Busca de antemão as fotos livres das sugestões (só online): o usuário abre a cena e já vê os candidatos.
    Nunca derruba o job."""
    if get_settings().offline:
        return
    if not any(s.real is not None and s.real.kind == "photo" and s.real.query.strip() and s.visual.media is None
               for s in doc.scenes):
        return
    ctx.progress(0.9, "Buscando fotos livres para as sugestões")
    try:
        media_search.prefetch(doc, style, budget_s=25)
    except Exception as exc:  # noqa: BLE001 - a busca é só uma ajuda
        log.warning("a pré-busca de fotos livres falhou: %s", exc)


@job("translate")
def run_translate(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    targets: list[str] = payload.get("langs") or []
    # Só os textos de tela que faltam (cartões e sobreposições criados depois da tradução): a narração fica.
    screen_only = bool(payload.get("screen_only"))
    doc = service.get_doc(project_id)
    targets = targets or [lang for lang in doc.languages if lang != doc.primary_language]
    if payload.get("shorten"):
        # Modo B: só encurta os trechos que passam do tempo (correções manuais e trechos travados ficam).
        shortened: dict[str, list[str]] = {}
        for index, lang in enumerate(targets):
            ctx.progress(index / max(1, len(targets)), f"Encurtando a tradução em {authoring.lang_name(lang)}")
            doc, ids = authoring.shorten_translation(doc, lang, project_id=project_id, job_id=ctx.job_id)
            if ids:
                shortened[lang] = ids
        total = sum(len(ids) for ids in shortened.values())
        version = service.commit(project_id, doc, f"Tradução encurtada para caber no tempo ({total} trechos)",
                                 author="ai") if shortened else None
        return {"version": version, "langs": sorted(shortened), "shortened": shortened}
    glossary = channel_glossary(doc.channel_id)
    done = []
    for index, lang in enumerate(targets):
        ctx.progress(index / max(1, len(targets)), f"Traduzindo para {authoring.lang_name(lang)}")
        doc, _provider = authoring.translate(doc, lang, screen_only=screen_only, project_id=project_id, job_id=ctx.job_id,
                                             glossary=glossary)
        done.append(lang)
    message = f"{'Tradução dos textos de tela' if screen_only else 'Tradução'}: {', '.join(done)}"
    version = service.commit(project_id, doc, message, author="ai") if done else None
    result: dict[str, Any] = {"version": version, "langs": done}
    # Conferência do glossário: o aviso aparece no roteiro (o detalhe do projeto refaz a conta a cada abertura).
    issues = authoring.glossary_issues(doc, glossary, done)
    if issues:
        result["glossary_warnings"] = [f"{i['lang']} · {i['id']}: {i['message']}" for i in issues]
    return result


@job("metadata")
def run_metadata(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    langs = payload.get("langs") or doc.languages
    chapters = payload.get("chapters") or []
    lessons = _project_lessons(doc, style)
    for index, lang in enumerate(langs):
        ctx.progress(index / max(1, len(langs)), f"Metadados em {authoring.lang_name(lang)}")
        doc, _ = authoring.metadata(doc, style, lang, chapters=chapters, project_id=project_id, job_id=ctx.job_id,
                                    lessons=lessons)
    version = service.commit(project_id, doc, "Metadados (título, descrição, tags)", author="ai")
    _record_lessons(project_id, "metadata", lessons, version)
    return {"version": version, "langs": langs}


def _build(ctx: JobContext, payload: dict[str, Any], mode: str) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    version = service.get_project_row(project_id)["current_version"]
    builder = Builder(project_id, mode=mode, langs=payload.get("langs"), sink=ctx)  # type: ignore[arg-type]
    result = {**builder.run(), "version": version}
    result["built_at"] = utcnow().isoformat()  # ordem de renderização (música sem repetir no canal)
    service.update_settings(project_id, {f"last_{mode}": result})
    # Impressão digital para o verificador de novidade: feita aqui, no job, e não na hora de conferir.
    novelty.refresh(project_id)
    if mode == "final":
        # Conferência de novidade (só aviso): imagens, miniatura e título parecidos com os últimos vídeos do canal.
        extra = [w for w in novelty.safe_warnings(project_id) if w not in result["warnings"]]
        if extra:
            result["warnings"] = [*result["warnings"], *extra]
            service.update_settings(project_id, {f"last_{mode}": result})
    service.set_status(project_id, "preview_ready" if mode == "preview" else "rendered")
    # Rotina: com limite de espaço e limpeza automática ligados, o cache volta ao limite depois do build.
    from studio.core.graph import gc

    gc.auto_clean(exclude_job=ctx.job_id)
    return result


class _PartSink:
    """Progresso de uma parte do job (ex.: a 2ª de 2 versões) dentro da barra do job inteiro."""

    def __init__(self, ctx: JobContext, start: float, size: float, label: str) -> None:
        self.ctx, self.start, self.size, self.label = ctx, start, size, label

    def progress(self, fraction: float, message: str = "") -> None:
        self.ctx.progress(self.start + self.size * fraction, f"{self.label}: {message}" if message else self.label)

    def cancelled(self) -> bool:
        return self.ctx.cancelled()

    @property
    def job_id(self) -> str | None:
        return self.ctx.job_id


@job("ab_build")
def run_ab_build(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prévia ou vídeo final de uma ou mais versões A/B (`branches`: "main", "b"...), uma depois da outra.

    Cada ramo grava na pasta dele (`outputs/<projeto>/ramos/<nome>`); a principal grava no lugar de sempre.
    """
    from studio.core.project import branches as br

    project_id = ctx.project_id
    assert project_id
    mode = "final" if payload.get("mode") == "final" else "preview"
    names = list(dict.fromkeys(payload.get("branches") or [br.MAIN]))
    infos = [br.get_branch(project_id, name) for name in names]
    results: dict[str, Any] = {}
    for index, info in enumerate(infos):
        if ctx.cancelled():
            raise BuildCancelled("cancelado pelo usuário")
        label = f"Versão {info['letter']}"
        sink = _PartSink(ctx, index / len(infos), 1 / len(infos), label)
        version = info["head"]
        builder = Builder(project_id, mode=mode, langs=payload.get("langs"), sink=sink,  # type: ignore[arg-type]
                          version=None if info["is_main"] else version, branch=info["name"])
        result = builder.run()
        result["built_at"] = utcnow().isoformat()  # ordem de renderização (música sem repetir no canal)
        br.save_result(project_id, info["name"], mode, result, version)
        if info["is_main"]:
            service.set_status(project_id, "preview_ready" if mode == "preview" else "rendered")
        results[info["name"]] = {"letter": info["letter"], "version": version, "langs": result["langs"],
                                 "generated": result["generated"], "reused": result["reused"]}
    ctx.progress(1.0, "Versões prontas")
    return {"mode": mode, "branches": results}


@job("preview")
def run_preview(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _build(ctx, payload, "preview")


@job("render")
def run_render(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _build(ctx, payload, "final")


@job("video_clips")
def run_video_clips(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Gera o vídeo IA das cenas-chave, sem passar do valor confirmado pelo usuário (`max_cost_usd`)."""
    project_id = ctx.project_id
    assert project_id
    max_cost = float(payload.get("max_cost_usd") or 0.0)
    builder = Builder(project_id, mode="preview", sink=ctx)
    ctx.progress(0.0, "Gerando os clipes de vídeo IA")
    result = video_clips.generate(builder, max_cost)
    if not result["generated"] and not result["reused"] and result["errors"]:
        raise RuntimeError("nenhum clipe de vídeo IA saiu:\n- " + "\n- ".join(result["errors"][:10]))
    ctx.progress(1.0, "Clipes prontos: atualize a prévia para ver")
    return result


@job("autopilot")
def run_autopilot(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Roteiro → cenas → prévia (→ render), parando nos portões de aprovação ativos.

    O portão das cenas (`gates.storyboard`) só existe nos projetos novos: sem a chave, segue direto.
    """
    project_id = ctx.project_id
    assert project_id
    gates = _gates(project_id)
    steps: list[str] = []
    doc = service.get_doc(project_id)
    notes: list[str] = []
    if not doc.segments or payload.get("rewrite"):
        # Pacote (título e thumb antes do roteiro). Só projeto novo grava a chave `gates.package`: projeto antigo segue
        # direto. Longo com o portão ligado para até o pacote ser aprovado; Short e série geram sem parar. O portão só
        # vale antes do 1º roteiro: reescrever um roteiro que já existe não para no pacote (a aprovação dele não
        # retoma o piloto quando já há roteiro).
        style = _style_of(doc)
        package_ready = doc.package.locked and doc.package.chosen_id is not None
        if (package_gate_on(gates, style) and not package_ready and not doc.segments
                and not payload.get("skip_gates")):
            if package_missing(doc):
                run_package(ctx, payload)
                steps.append("package")
            service.set_status(project_id, PACKAGE_STATUS)
            return {"steps": steps, "stopped_at": "package", "message": "Título e thumb aguardando sua escolha"}
        if "package" in gates and package_missing(doc) and not doc.package.locked and not doc.segments:
            notes = _auto_package(ctx, payload)
            steps.append("package")
        run_script(ctx, payload)
        steps.append("script")
        if gates.get("script", True) and not payload.get("skip_gates"):
            out = {"steps": steps, "stopped_at": "script", "message": "Roteiro aguardando sua aprovação"}
            return {**out, "warnings": notes} if notes else out
    doc = service.get_doc(project_id)
    extra: dict[str, Any] = {"warnings": notes} if notes else {}
    if not doc.scenes:
        # No piloto, a busca entra no mesmo job (nada corre junto); o resultado e o aviso dela vão no do piloto.
        board = run_storyboard(ctx, payload, find_inline=True)
        extra = {**extra, **{k: board[k] for k in ("media_find", "warnings") if board.get(k)}}
        if notes and board.get("warnings"):
            extra["warnings"] = [*notes, *board["warnings"]]
        steps.append("storyboard")
        doc = service.get_doc(project_id)
    if gates.get("storyboard", False) and not doc.storyboard_approved and not payload.get("skip_gates"):
        service.set_status(project_id, SCENES_REVIEW_STATUS)
        return {"steps": steps, "stopped_at": "storyboard", "message": "Cenas aguardando sua aprovação", **extra}
    other_langs = [lang for lang in doc.languages if lang != doc.primary_language]
    doc = service.get_doc(project_id)
    # Narração que falta: tradução completa. Só textos de tela (cartões de cenas refeitas depois da tradução):
    # traduz só eles, sem refazer a narração nem a voz.
    full = [lang for lang in other_langs if any(not seg.text_for(lang).strip() for seg in doc.segments)]
    screen = [lang for lang in other_langs if lang not in full and authoring.needs_translation(doc, lang)]
    if full:
        run_translate(ctx, {"langs": full})
    if screen:
        run_translate(ctx, {"langs": screen, "screen_only": True})
    if full or screen:
        steps.append("translate")
    result = run_preview(ctx, payload)
    steps.append("preview")
    if gates.get("preview", True) and not payload.get("skip_gates"):
        return {"steps": steps, "stopped_at": "preview", "message": "Prévia aguardando sua aprovação", "preview": result,
                **extra}
    final = run_render(ctx, payload)
    steps.append("render")
    return {"steps": steps, "stopped_at": None, "render": final, **extra}
