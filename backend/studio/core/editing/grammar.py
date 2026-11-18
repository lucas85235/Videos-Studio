"""Do perfil medido ao alvo de edição (ADR 0004 §3.3).

`grammar_from_profile` transforma o `EditProfile` de uma referência no que o estilo guarda: a gramática de edição
(`EditGrammar`), a política de fontes (`SourcePolicy`), as trocas da legenda e do SFX. Só funções puras, fora o
`reference_profiles` (lê o perfil gravado em `reference_videos.stats["profile"]`).

Regras:
- campo marcado como padrão no `meta` (origem "padrão" ou confiança 0) não vale: fica o do estilo base; a entrada do
  campo ("pace.median_s") vale mais que a do bloco ("pace"), e campo sem nenhuma das duas é padrão;
- perfil sem `meta` nenhum: os blocos que o modo rápido mede (ritmo, câmera, áudio) valem quando o vídeo foi medido
  (confiança assumida 0,5); os que dependem da visão e da fala (materiais, sobreposições, legenda, fala, humor) só no
  modo profundo;
- a tolerância das cotas cresce quando a confiança média é baixa;
- `materials.mix` vira `sources.mix` pelo `MATERIAL_TO_PROV`, com `ai_max_share = max(0,05, mix.ai)`;
- facecam ou mascote acima de 10% do tempo sugerem ligar o apresentador;
- a câmera medida (Ken Burns/pan, parada, zoom lento, punch-in) vira pesos de `edit.camera`, que é o que move a
  imagem no render de hoje;
- classes de SFX viram só tipos que o app toca (pop → impacto, scratch → glitch; "other" fica de fora), e o SFX
  automático liga com SFX fora das transições OU nos cortes;
- a janela do gancho é 20% da duração (entre 3 e 30 s), não os 30 s fixos da medida.

`script_reference_block` monta o bloco "Voz e estrutura da referência" do pedido do roteiro: andamento da fala,
pessoa, tom, papéis em proporção (e convertidos para a duração-alvo) e padrões de humor ABSTRATOS, só dos campos
medidos, com a regra de nunca usar frase da referência.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from statistics import median
from typing import Any

from studio.config import REPO_ROOT
from studio.core.editing.profile import EditProfile, FieldMeta, presenter_share, prov_mix_from_materials
from studio.core.styles.schema import EditGrammar, HookGrammar, Range, SourcePolicy

log = logging.getLogger(__name__)

# Blocos que o modo rápido (sem visão nem fala) também mede.
QUICK_BLOCKS = frozenset({"pace", "camera", "audio"})
# Confiança assumida para um campo medido sem entrada no `meta`.
DEFAULT_CONFIDENCE = 0.5
PRESENTER_SUGGEST_SHARE = 0.10
AI_FLOOR = 0.05
"""Teto mínimo de IA: mesmo com a referência sem nenhuma, sobra espaço para uma hipótese ou um conceito."""
HUMOR_PATTERNS_MAX = 4
FREE_FONT_LICENSES = ("OFL", "Apache")
FONTS_CATALOG = REPO_ROOT / "assets" / "fonts" / "fonts.json"
# Classe de SFX do perfil → tipo que o kit procedural toca (`media/audio.SFX_KINDS`). "other" não é tipo: fica de fora.
SFX_CLASS_TO_KIND: dict[str, str] = {
    "impact": "impact", "whoosh": "whoosh", "riser": "riser", "pop": "impact", "scratch": "glitch",
}
# Janela do gancho: o perfil mede os primeiros 30 s, mas num vídeo curto isso é quase o vídeo todo; a janela fica em
# 20% da duração, entre 3 e 30 s (sem duração, a do contrato).
HOOK_MEASURE_S = 30.0
HOOK_SHARE = 0.2
HOOK_MIN_S = 3.0
# Câmera: o punch-in ocupa no máximo esta fração das cenas.
PUNCH_SCENE_MAX = 0.3
KB_MOVES = ("ken_burns_in", "ken_burns_out", "pan_left", "pan_right", "pan_up", "pan_down")


@dataclass(frozen=True)
class ProfileGrammar:
    """O que o perfil diz para o estilo. `captions` e `sfx` são trocas relativas a `edit.captions` e `audio.sfx`
    (só os campos medidos); `details` explica cada valor em palavras simples, para a tela de origens."""

    grammar: EditGrammar
    sources: SourcePolicy
    captions: dict[str, Any] = field(default_factory=dict)
    sfx: dict[str, Any] = field(default_factory=dict)
    camera: dict[str, float] = field(default_factory=dict)
    """Pesos de `edit.camera` medidos (Ken Burns/pan, parada, push, punch); vazio: fica a câmera do estilo."""
    text_card_font: str | None = None
    """Fonte livre mais próxima da fonte dos textos na tela (`overlays.font_desc`), para `edit.text_cards.font`."""
    sources_measured: bool = False
    """A política saiu da mistura medida (modo profundo). Falso: é a do estilo base."""
    grammar_fields: tuple[str, ...] = ()
    """Campos da gramática que vieram do perfil."""
    presenter_share: float = 0.0
    suggest_presenter: bool = False
    confidence: float = 0.0
    """Confiança média dos campos usados."""
    details: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------------------------
# Confiança por campo
# --------------------------------------------------------------------------------------------


def _meta(profile: EditProfile, path: str) -> FieldMeta | None:
    """O `meta` do campo ("pace.median_s") ou, sem ele, o do bloco ("pace")."""
    return profile.meta.get(path) or profile.meta.get(path.split(".")[0])


def _video_measured(profile: EditProfile) -> bool:
    return profile.duration_s > 0 or profile.pace.shots > 0


def is_measured(profile: EditProfile, path: str) -> bool:
    """O campo do perfil é medida de verdade (e não o valor padrão do modelo)?"""
    meta = _meta(profile, path)
    if meta is not None:
        return meta.origin != "padrão" and meta.confidence > 0
    if profile.meta:
        return False  # o perfil traz `meta`: campo sem entrada é padrão (contrato do EditProfile)
    block = path.split(".")[0]
    if block in QUICK_BLOCKS:
        return _video_measured(profile)
    return not profile.quick


def confidence_of(profile: EditProfile, path: str) -> float:
    meta = _meta(profile, path)
    return meta.confidence if meta is not None else DEFAULT_CONFIDENCE


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _r(value: float, nd: int = 2) -> float:
    return round(float(value), nd)


def num(value: float) -> str:
    """Número para a tela, com vírgula decimal (1,43; 4,2; 30)."""
    return f"{float(value):g}".replace(".", ",")


# --------------------------------------------------------------------------------------------
# Fonte da legenda: a livre mais próxima do catálogo (Pricedown nunca: não é livre e não está no catálogo)
# --------------------------------------------------------------------------------------------

# Palavras da descrição → fonte do catálogo, na ordem de preferência em caso de empate. Palavra terminada em "*" é
# radical e casa por prefixo ("datilograf*" casa "datilografada" e "datilografia"); as outras casam inteiras.
FONT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Anton", ("anton", "pricedown", "gta", "impact", "heavy", "pesad*", "gross*", "black", "ultra", "poster*",
               "cartaz*", "display", "condensed bold", "condensada pesada")),
    ("Bebas Neue", ("bebas", "caixa alta condensada", "all caps", "caps", "titulo*", "headline*", "manchete*")),
    ("Oswald", ("oswald", "condensed", "condensad*", "estreit*", "narrow", "jorna*", "news*", "noticia*")),
    ("Montserrat", ("montserrat", "geometric*", "rounded", "arredondad*", "modern*", "bold sans", "sans bold",
                    "negrito", "youtube*", "tiktok*")),
    ("Cinzel", ("cinzel", "serif*", "romana*", "romano*", "roman", "classic*", "trajan", "epic*", "medieva*",
                "historic*")),
    ("Special Elite", ("special elite", "typewriter*", "maquina de escrever", "datilograf*", "carimb*", "stamp*")),
    ("VT323", ("vt323", "pixel*", "retro*", "8-bit", "8 bit", "terminal*", "arcade*", "crt", "lcd")),
    ("IBM Plex Sans", ("plex", "tech*", "tecnic*", "code", "codigo*", "mono", "monospace*", "monoespacad*",
                       "interface*", "ui")),
    ("Noto Sans", ("noto", "neutr*", "limp*", "clean", "simples", "simple", "helvetica", "arial", "sans")),
)
# "sans serif" / "sem serifa" é o contrário de serifa: vira "sans" antes de casar (senão "serif*" puxaria a Cinzel).
_NO_SERIF = re.compile(r"(?<![a-z0-9])(?:sans[ -]?serif\w*|sem serifa\w*)")


def _fold(text: str) -> str:
    import unicodedata

    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", norm).strip()


@lru_cache(maxsize=1)
def font_catalog() -> dict[str, dict[str, Any]]:
    """Fontes do app (`assets/fonts/fonts.json`) com licença livre (OFL ou Apache)."""
    try:
        data = json.loads(FONTS_CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: info for name, info in data.items()
            if str(info.get("license") or "").startswith(FREE_FONT_LICENSES)}


def match_font(description: str, catalog: dict[str, dict[str, Any]] | None = None) -> str | None:
    """A fonte livre do catálogo mais próxima de um nome ou descrição ("Pricedown, pesada, estilo GTA" → Anton).
    None quando nada bate (fica a fonte do estilo)."""
    catalog = font_catalog() if catalog is None else catalog
    text = _fold(description or "")
    if not text or not catalog:
        return None
    for name in catalog:
        if _fold(name) == text:
            return name
    text = _NO_SERIF.sub("sans", text)
    best: tuple[int, int, str] | None = None
    for order, (name, words) in enumerate(FONT_KEYWORDS):
        if name not in catalog:
            continue
        hits = sum(1 for w in words if _keyword_hit(w, text))
        if hits and (best is None or (hits, -order) > (best[0], best[1])):
            best = (hits, -order, name)
    return best[2] if best else None


def _keyword_hit(word: str, text: str) -> bool:
    if word.endswith("*"):
        return re.search(rf"(?<![a-z0-9]){re.escape(word[:-1])}", text) is not None
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def free_font(name: str | None) -> bool:
    """O nome é de uma fonte livre do catálogo (o render só tem essas)."""
    return bool(name) and name in font_catalog()


def _font_weight(name: str, current: int, catalog: dict[str, dict[str, Any]] | None = None) -> int:
    catalog = font_catalog() if catalog is None else catalog
    weights = (catalog.get(name) or {}).get("weights") or [current, current]
    lo, hi = int(weights[0]), int(weights[-1])
    return int(_clip(current, lo, hi))


# --------------------------------------------------------------------------------------------
# Perfil → gramática, política de fontes, legenda e SFX
# --------------------------------------------------------------------------------------------


def hook_window_s(duration_s: float) -> float:
    """Janela do gancho: 20% da duração, entre 3 e 30 s (o perfil mede os 30 s iniciais). Sem duração: 5 s."""
    if duration_s <= 0:
        return HookGrammar().window_s
    return _r(_clip(duration_s * HOOK_SHARE, HOOK_MIN_S, HOOK_MEASURE_S), 1)


def sfx_palette_from_classes(classes: dict[str, float]) -> dict[str, float]:
    """Classes de SFX do perfil → pesos por tipo que o app toca (pop vira impacto, scratch vira glitch; "other"
    fica de fora)."""
    out: dict[str, float] = {}
    for cls, share in classes.items():
        kind = SFX_CLASS_TO_KIND.get(cls)
        if kind is not None and share > 0:
            out[kind] = out.get(kind, 0.0) + share
    return {k: _r(v, 3) for k, v in out.items()}


def camera_from_profile(profile: EditProfile, base_camera: dict[str, float] | None,
                        shot_median_s: float) -> dict[str, float]:
    """Pesos de `edit.camera` (um movimento por cena) a partir do tempo medido: Ken Burns e pan pelo `kb_share`
    (divididos como no estilo base), "static" pelo `static_share`, "push" pelo zoom lento, "punch_zoom" pelos
    punch-ins por cena e "drift" no que sobra. Vazio quando a câmera não foi medida."""
    cam = profile.camera
    if not (is_measured(profile, "camera.kb_share") and is_measured(profile, "camera.static_share")):
        return {}
    kb, still = cam.kb_share, cam.static_share
    rest = max(0.0, 1.0 - kb - still)
    push = min(cam.slow_zoom_share, rest) if is_measured(profile, "camera.slow_zoom_share") else 0.0
    punch = 0.0
    if is_measured(profile, "camera.punch_per_min") and cam.punch_per_min > 0:
        punch = min(PUNCH_SCENE_MAX, cam.punch_per_min * shot_median_s / 60, max(0.0, rest - push))
    drift = max(0.0, rest - push - punch)
    base_kb = {k: v for k, v in (base_camera or {}).items() if k in KB_MOVES and v > 0}
    if not base_kb:
        base_kb = {"ken_burns_in": 2.0, "ken_burns_out": 1.0, "pan_left": 1.0, "pan_right": 1.0}
    total_kb = sum(base_kb.values())
    weights: dict[str, float] = {k: kb * v / total_kb for k, v in base_kb.items()}
    weights.update({"static": still, "push": push, "punch_zoom": punch, "drift": drift})
    out = {k: _r(v * 10, 2) for k, v in weights.items() if _r(v * 10, 2) > 0}
    return out or {"drift": 1.0}


def _sources_from_mix(mix: dict[str, float], base: SourcePolicy) -> SourcePolicy | None:
    prov = prov_mix_from_materials(mix)
    if not prov:
        return None
    ai = prov.get("ai", 0.0)
    # Material oficial além do teto (risco de Content ID): o excesso vai para os outros níveis reais, na proporção
    # (meme e IA não ganham tempo com isso).
    cap = base.official_max_share
    excess = max(0.0, prov.get("official", 0.0) - cap)
    if excess > 0:
        prov["official"] = cap
        others = {k: v for k, v in prov.items() if k not in ("official", "ai", "meme") and v > 0}
        total = sum(others.values())
        if total > 0:
            for key, share in others.items():
                prov[key] = share + excess * share / total
        else:
            prov["graphic"] = prov.get("graphic", 0.0) + excess
    out = {k: _r(v, 3) for k, v in prov.items() if v > 0}
    out["ai"] = _r(ai, 3)
    return base.model_copy(update={"mix": out, "ai_max_share": _r(max(AI_FLOOR, ai), 3)})


def grammar_from_profile(profile: EditProfile, *, base: EditGrammar | None = None,
                         base_sources: SourcePolicy | None = None,
                         ref_ids: list[str] | None = None,
                         base_camera: dict[str, float] | None = None) -> ProfileGrammar:
    """O alvo de edição medido na referência, sobre a gramática e a política do estilo base (o que o perfil não mede
    fica como estava). `ref_ids` vai para `grammar.profile_refs` (sem ele, `profile.refs`). `base_camera` (os pesos de
    `edit.camera` do estilo base) decide como o tempo de Ken Burns/pan se divide entre os movimentos.

    Passe como `base`/`base_sources` os EFETIVOS do formato (`grammar_for`/`sources_for`): com `profile_refs`, o Short
    não troca mais nada (o que veio do perfil é medida), então o que o perfil não mediu já precisa estar no valor
    do Short."""
    base = base or EditGrammar()
    base_sources = base_sources or SourcePolicy()
    upd: dict[str, Any] = {}
    details: dict[str, str] = {}
    confs: list[float] = []

    def take(path: str) -> bool:
        if is_measured(profile, path):
            confs.append(confidence_of(profile, path))
            return True
        return False

    pace, cam, ov, aud = profile.pace, profile.camera, profile.overlays, profile.audio

    # Ritmo dos planos.
    if pace.median_s > 0 and take("pace.median_s"):
        upd["shot_median_s"] = _r(_clip(pace.median_s, 0.3, 30))
        details["shot_median_s"] = f"plano mediano de {num(pace.median_s)} s"
    if pace.cv > 0 and take("pace.cv"):
        upd["shot_cv"] = _r(_clip(pace.cv, 0, 3))
    med = upd.get("shot_median_s", base.shot_median_s)
    if pace.p90_s > 0 and take("pace.p90_s"):
        # Teto do plano: 1,5× o p90 (o plano mais longo da referência costuma ser exceção, um meme ou um depoimento).
        top = max(pace.p90_s * 1.5, med * 2)
        if pace.max_shot_s > 0:
            top = min(top, max(pace.max_shot_s, med))
        upd["shot_max_s"] = _r(_clip(top, max(0.5, med), 60), 1)
    if take("pace.micro_per_min"):
        upd["micro_per_min"] = _r(_clip(pace.micro_per_min, 0, 60))
        details["micro_per_min"] = f"{num(pace.micro_per_min)} micro-planos (< 0,4 s) por minuto"
    if pace.max_still_s > 0 and take("pace.max_still_s"):
        cap = max(3.0, 2 * upd.get("shot_max_s", base.shot_max_s))
        upd["max_still_s"] = _r(_clip(min(pace.max_still_s, cap), 0.5, 60), 1)
    if pace.hook_cuts_per_min > 0 and pace.cuts_per_min > 0 and take("pace.hook_cuts_per_min"):
        ratio = pace.hook_cuts_per_min / pace.cuts_per_min
        if ratio >= 1.25 or ratio <= 0.8:  # o gancho tem ritmo próprio
            upd["hook"] = HookGrammar(window_s=hook_window_s(profile.duration_s),
                                      shot_median_s=_r(_clip(med / ratio, 0.3, 30)))
            details["hook"] = (f"gancho com {num(pace.hook_cuts_per_min)} cortes/min "
                               f"(o vídeo: {num(pace.cuts_per_min)})")

    # Câmera.
    if take("camera.punch_per_min"):
        upd["punch_per_min"] = _r(_clip(cam.punch_per_min, 0, 60))
        details["punch_per_min"] = f"{num(cam.punch_per_min)} punch-ins por minuto"
    if cam.punch_scale > 0 and take("camera.punch_scale"):
        lo = _r(_clip(1 + cam.punch_scale * 0.75, 1.02, 1.8))
        hi = _r(_clip(1 + cam.punch_scale * 1.25, lo, 1.8))
        upd["punch_scale"] = Range(min=lo, max=hi)
    if take("camera.shake_per_min"):
        upd["shake_per_min"] = _r(_clip(cam.shake_per_min, 0, 60))
    if take("camera.slow_zoom_share"):
        upd["slow_zoom_share"] = _r(cam.slow_zoom_share)
    if take("camera.static_share"):
        upd["static_share_min"] = _r(cam.static_share)

    # Sobreposições e trocas de material (visão).
    if take("overlays.text_pop_per_min"):
        upd["text_pop_per_min"] = _r(_clip(ov.text_pop_per_min, 0, 60))
    if take("overlays.meme_per_min"):
        upd["meme_per_min"] = _r(_clip(ov.meme_per_min, 0, 30))
        details["meme_per_min"] = f"{num(ov.meme_per_min)} memes por minuto"
    if take("overlays.annot_per_min"):
        upd["annot_per_min"] = _r(_clip(ov.annot_per_min, 0, 30))
    if take("materials.switch_per_min"):
        upd["switch_per_min"] = _r(_clip(profile.materials.switch_per_min, 0, 60))

    # Áudio.
    if take("audio.sfx_per_min"):
        upd["sfx_per_min"] = _r(_clip(aud.sfx_per_min, 0, 120))
        details["sfx_per_min"] = f"{num(aud.sfx_per_min)} SFX por minuto fora das transições"
    if take("audio.sfx_on_cut_share"):
        upd["sfx_on_cut_share"] = _r(aud.sfx_on_cut_share)
    if aud.sfx_classes and take("audio.sfx_classes"):
        palette = sfx_palette_from_classes(aud.sfx_classes)
        if palette:
            upd["sfx_palette"] = palette
    if take("audio.drops_per_min"):
        upd["music_drop_before_reveal"] = aud.drops_per_min > 0

    # Humor e ênfase na legenda.
    mix = profile.materials.mix
    meme_signal = None
    if is_measured(profile, "overlays.meme_per_min") or is_measured(profile, "materials.mix"):
        meme_signal = ov.meme_per_min >= 0.5 or mix.get("meme_reaction", 0.0) >= 0.05
    if meme_signal:
        upd["humor"] = "meme"
    elif is_measured(profile, "humor.patterns") or is_measured(profile, "humor.gag_per_min"):
        upd["humor"] = "light" if (profile.humor.patterns or profile.humor.gag_per_min > 0) else "none"
    if profile.captions.emphasis and take("captions.emphasis"):
        upd["caption_emphasis"] = profile.captions.emphasis

    measured_fields = tuple(upd)
    if confs:
        # Confiança baixa: a aderência aceita mais desvio (0,35 com confiança 1; 1,05 com confiança 0).
        grammar_conf = sum(confs) / len(confs)
        upd["tolerance"] = _r(_clip(base.tolerance * (1 + 2 * (1 - grammar_conf)), 0.05, 3))
    refs = list(dict.fromkeys(ref_ids if ref_ids is not None else profile.refs))
    if refs:
        upd["profile_refs"] = refs
    grammar = EditGrammar.model_validate({**base.model_dump(), **{
        k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in upd.items()}})

    # Política de fontes.
    sources, sources_measured = base_sources, False
    if mix and is_measured(profile, "materials.mix"):
        measured = _sources_from_mix(mix, base_sources)
        if measured is not None:
            sources, sources_measured = measured, True
            confs.append(confidence_of(profile, "materials.mix"))
            ai_pct = round(measured.mix.get("ai", 0.0) * 100)
            details["sources"] = (f"IA em {ai_pct}% do tempo de tela na referência; teto de IA "
                                  f"{round(measured.ai_max_share * 100)}%")
    share = presenter_share(mix) if is_measured(profile, "materials.mix") else 0.0

    # Legenda.
    captions: dict[str, Any] = {}
    cap = profile.captions
    if cap.style and take("captions.style"):
        captions["style"] = cap.style
    if cap.words_per_line > 0 and take("captions.words_per_line"):
        captions["max_words_per_line"] = int(_clip(round(cap.words_per_line), 1, 12))
    if cap.uppercase is not None and take("captions.uppercase"):
        captions["uppercase"] = cap.uppercase
    if cap.position and take("captions.position"):
        captions["position"] = cap.position
    font_desc = cap.font_match or cap.font_desc
    if font_desc and (is_measured(profile, "captions.font_match") or is_measured(profile, "captions.font_desc")):
        font = match_font(font_desc)
        if font:
            captions["font"] = font
            details["font"] = f"fonte livre mais próxima de “{font_desc[:60]}”"
    # Textos na tela (cartões): a fonte livre mais próxima da medida.
    text_card_font = None
    if ov.font_desc and is_measured(profile, "overlays.font_desc"):
        text_card_font = match_font(ov.font_desc)
        if text_card_font:
            details["text_card_font"] = f"fonte livre mais próxima de “{ov.font_desc[:60]}”"

    # SFX automático: liga com SFX fora das transições OU nos cortes (sem ele, a timeline cala os dois).
    sfx: dict[str, Any] = {}
    if is_measured(profile, "audio.sfx_per_min") or is_measured(profile, "audio.sfx_on_cut_share"):
        sfx["auto"] = aud.sfx_per_min > 0 or aud.sfx_on_cut_share > 0

    # Câmera (o que move a imagem no render hoje): pesos de `edit.camera` pelo tempo medido.
    camera = camera_from_profile(profile, base_camera, grammar.shot_median_s)
    if camera:
        details["camera"] = (f"Ken Burns ou pan em {round(cam.kb_share * 100)}% do tempo, câmera parada em "
                             f"{round(cam.static_share * 100)}%, {num(cam.punch_per_min)} punch-ins por minuto")

    conf = sum(confs) / len(confs) if confs else 0.0
    return ProfileGrammar(
        grammar=grammar, sources=sources, captions=captions, sfx=sfx, camera=camera, text_card_font=text_card_font,
        sources_measured=sources_measured,
        grammar_fields=measured_fields, presenter_share=_r(share, 3),
        suggest_presenter=share > PRESENTER_SUGGEST_SHARE, confidence=_r(conf, 3), details=details,
    )


def caption_weight(font: str, current: int) -> int:
    """O peso da legenda dentro da faixa que a fonte tem (Anton só tem 400)."""
    return _font_weight(font, current)


# --------------------------------------------------------------------------------------------
# Roteiro: "Voz e estrutura da referência"
# --------------------------------------------------------------------------------------------

PERSON_TEXT = {
    "1": "primeira pessoa (\"eu\", \"a gente\"), como quem conta para um amigo",
    "2": "falando direto com o espectador (\"você\")",
    "3": "narrador em terceira pessoa",
}


def reference_profiles(ref_ids: list[str]) -> list[EditProfile]:
    """Os perfis gravados (`reference_videos.stats["profile"]`) das referências, na ordem; perfil ausente ou inválido
    fica de fora."""
    if not ref_ids:
        return []
    from studio.db import session_scope
    from studio.models import ReferenceVideo

    out: list[EditProfile] = []
    with session_scope() as s:
        for ref_id in ref_ids:
            row = s.get(ReferenceVideo, ref_id)
            raw = (row.stats or {}).get("profile") if row is not None else None
            if not isinstance(raw, dict):
                continue
            try:
                out.append(EditProfile.model_validate(raw))
            except ValueError as exc:
                log.warning("perfil inválido na referência %s: %s", ref_id, exc)
    return out


def _structure_line(profile: EditProfile, target_s: float | None = None) -> str:
    """Papéis da referência em proporção do vídeo. Com `target_s` (a duração-alvo do estilo), os tempos saem
    convertidos para o vídeo novo; os segundos absolutos da referência nunca vão (um gancho de 30 s num vídeo de
    13 min seria mais da metade de um Short)."""
    beats = [b for b in profile.speech.structure if b.end_s > b.start_s]
    total = profile.duration_s or (max(b.end_s for b in beats) if beats else 0)
    if not beats or total <= 0:
        return ""

    def frac(t: float) -> float:
        return min(t, total) / total

    def part(b: Any) -> str:
        a, z = frac(b.start_s), frac(b.end_s)
        text = f"{b.role[:30]} ({round(a * 100)}–{round(z * 100)}%"
        if target_s and target_s > 0:
            text += f", ~{a * target_s:.0f}–{z * target_s:.0f} s do vídeo novo"
        return text + ")"

    return " → ".join(part(b) for b in beats[:10])


def _dominant(values: list[str]) -> str:
    """O mais frequente; no empate, o que aparece primeiro (determinístico, sem depender do hash das strings)."""
    return max(dict.fromkeys(values), key=values.count)


def script_reference_block(profiles: list[EditProfile], target_s: float | None = None) -> str:
    """O bloco "Voz e estrutura da referência" do pedido do roteiro. Vazio quando o perfil não tem nada de fala, tom,
    estrutura ou humor medidos (modo rápido, ou campos marcados como padrão no `meta`). `target_s` (a duração-alvo do
    estilo) converte os tempos dos papéis para o vídeo novo."""
    if not profiles:
        return ""
    lines: list[str] = []
    wpms = [p.speech.wpm for p in profiles if p.speech.wpm > 0 and is_measured(p, "speech.wpm")]
    if wpms:
        lines.append(f"- Andamento da fala: cerca de {round(median(wpms))} palavras por minuto na referência (o "
                     "tamanho do roteiro é o pedido acima; isto diz só o ritmo das frases).")
    persons = [p.speech.person for p in profiles if p.speech.person and is_measured(p, "speech.person")]
    if persons:
        person = _dominant(persons)
        lines.append(f"- Pessoa: {PERSON_TEXT.get(person, person)}.")
    tags = list(dict.fromkeys(t.strip() for p in profiles if is_measured(p, "speech.tone_tags")
                              for t in p.speech.tone_tags if t.strip()))[:6]
    if tags:
        lines.append(f"- Tom: {', '.join(t[:40] for t in tags)}.")
    structure = next((line for line in (_structure_line(p, target_s) for p in profiles
                                        if is_measured(p, "speech.structure")) if line), "")
    if structure:
        lines.append(f"- Papéis e tempos (proporção do vídeo): {structure}.")
    patterns = list(dict.fromkeys(x.strip() for p in profiles if is_measured(p, "humor.patterns")
                                  for x in p.humor.patterns if x.strip()))
    patterns = patterns[:HUMOR_PATTERNS_MAX]
    if patterns:
        lines.append("- Padrões de humor (só o mecanismo, com conteúdo novo):")
        lines += [f"  - {x[:200]}" for x in patterns]
    if not lines:
        return ""
    return ("Voz e estrutura da referência (o canal quer soar assim; copie a forma, não o conteúdo):\n"
            + "\n".join(lines)
            + "\nRegra: nenhuma frase da referência. Nada de citar, traduzir ou parafrasear de perto o que foi dito "
              "nela; use só o ritmo, a pessoa, o tom, a estrutura e os mecanismos de humor acima.")
