"""Modo B (README §7.3): várias faixas de idioma no mesmo vídeo (só YouTube).

A imagem é uma só (a do idioma principal) e cada idioma ganha uma faixa de áudio própria que precisa ter
praticamente a mesma duração do vídeo. Para isso:

1. a tradução recebe um **orçamento de duração por segmento** (o texto traduzido cabe no tempo do original);
2. cada segmento ganha um **tempo de vaga**: a duração da narração principal, esticada só quando a de outro idioma
   não cabe nem acelerada até `MAX_SPEEDUP` (o idioma mais longo define o tempo do segmento);
3. a narração que passa da vaga é acelerada com o filtro `atempo` do FFmpeg (muda a velocidade sem mudar o tom),
   no máximo `MAX_SPEEDUP`; fora disso o app avisa (o vídeo ganha uma pausa naquele trecho). A que fica curta é
   desacelerada até `MIN_SLOWDOWN` para não sobrar silêncio no fim do trecho;
4. cada idioma é mixado sobre a MESMA timeline (mesma trilha, mesmos efeitos, mesmos inícios de segmento);
5. o pacote para o YouTube Studio junta, por idioma, o WAV mixado, a legenda SRT, o título e a descrição, com um
   checklist. A API do YouTube não aceita faixas de áudio extras: elas vão à mão em Studio → Idiomas → Dublagem.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.core.project.document import ProjectDoc
from studio.core.styles.schema import StyleSpec
from studio.media.ffmpeg import to_wav
from studio.providers.base import Word

MULTI_TRACK = "multi_audio_track"

MAX_SPEEDUP = 1.10
"""Aceleração máxima da narração traduzida (+10%, README §7.3). Acima disso a voz soa apressada."""
MIN_SLOWDOWN = 0.92
"""Desaceleração máxima da narração traduzida curta (-8%, README §7.3). Abaixo disso a voz soa arrastada."""
TEMPO_STEP = 0.005
"""Ajuste menor que isso não é aplicado (`to_wav` ignora o `atempo` abaixo dele): conta como 1.0."""
PAUSE_TOLERANCE_S = 0.25
"""Folga (s) que o segmento pode crescer sem aviso (arredondamento, respiro)."""
SHORT_RATIO = 0.7
"""Faixa traduzida com menos de 70% do tempo do segmento: silêncio longo no fim do trecho (aviso)."""
SHORT_MIN_GAP_S = 1.5

BUDGET_TOLERANCE = MAX_SPEEDUP
"""A tradução pode passar do orçamento até o que a aceleração cobre; acima disso, pede para encurtar."""

# Idiomas sem espaço entre palavras: duração estimada por caracteres falados por segundo.
_CHARS_PER_SECOND = {"ja": 7.5, "zh": 4.5}
_DEFAULT_WPM = 150
_TOKEN_RE = re.compile(r"\w+(?:[-'’]\w+)*", re.UNICODE)


def is_multi_track(style: StyleSpec, doc: ProjectDoc) -> bool:
    """O projeto entrega os idiomas como faixas de áudio no mesmo vídeo (e tem mais de um idioma)?"""
    return style.languages.delivery == MULTI_TRACK and len(doc.languages) > 1


# --------------------------------------------------------------------------------------------
# Orçamento de duração da tradução
# --------------------------------------------------------------------------------------------


def _wpm(style: StyleSpec, lang: str) -> int:
    table = style.script.words_per_minute
    if lang in table:
        return table[lang]
    base = lang.split("-")[0].lower()
    for key, value in table.items():
        if key.split("-")[0].lower() == base:
            return value
    return _DEFAULT_WPM


def count_words(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def speech_seconds(text: str, lang: str, style: StyleSpec) -> float:
    """Duração estimada da fala (s) pelo ritmo do idioma (`script.words_per_minute`)."""
    base = lang.split("-")[0].lower()
    if base in _CHARS_PER_SECOND:
        spoken = sum(1 for ch in text if ch.isalnum())
        return spoken / _CHARS_PER_SECOND[base] / (style.voice.speed or 1.0)
    return count_words(text) / _wpm(style, lang) * 60 / (style.voice.speed or 1.0)


@dataclass
class Budget:
    segment_id: str
    seconds: float
    """Tempo que o segmento tem (estimado pelo texto original)."""
    max_words: int | None
    """Teto de palavras no idioma de destino (None nos idiomas medidos por caracteres)."""
    max_chars: int | None = None


def segment_budgets(doc: ProjectDoc, style: StyleSpec, lang: str) -> list[Budget]:
    """Orçamento de cada segmento traduzido para `lang`: o tempo do original e quantas palavras cabem nele."""
    source = doc.primary_language
    base = lang.split("-")[0].lower()
    out = []
    for seg in doc.segments:
        seconds = speech_seconds(seg.text_for(source), source, style)
        # `seconds` já está na velocidade da voz; o limite de texto volta à velocidade 1.0 (a mesma conta de
        # `speech_seconds`, que `over_budget` usa para conferir).
        spoken = seconds * (style.voice.speed or 1.0)
        if base in _CHARS_PER_SECOND:
            out.append(Budget(seg.id, seconds, None, max(1, int(spoken * _CHARS_PER_SECOND[base]))))
        else:
            out.append(Budget(seg.id, seconds, max(1, int(spoken * _wpm(style, lang) / 60))))
    return out


def budget_line(budget: Budget) -> str:
    limit = f"até {budget.max_words} palavras" if budget.max_words else f"até {budget.max_chars} caracteres"
    return f"{limit} (~{budget.seconds:.1f} s)"


def over_budget(doc: ProjectDoc, style: StyleSpec, lang: str, tolerance: float = BUDGET_TOLERANCE,
                *, skip_locked: bool = False) -> list[str]:
    """Segmentos cuja tradução passa do orçamento mais a folga que a aceleração cobre (com `skip_locked`, deixa de
    fora os trechos travados, que o app não reescreve)."""
    budgets = {b.segment_id: b for b in segment_budgets(doc, style, lang)}
    out = []
    for seg in doc.segments:
        if skip_locked and seg.locked:
            continue
        text = seg.text_for(lang)
        budget = budgets.get(seg.id)
        if not text.strip() or budget is None or budget.seconds <= 0:
            continue
        if speech_seconds(text, lang, style) > budget.seconds * tolerance + 0.3:
            out.append(seg.id)
    return out


def over_budget_report(doc: ProjectDoc, style: StyleSpec) -> dict[str, list[str]]:
    """Por idioma traduzido: os trechos (não travados) que passam do tempo. Serve para oferecer a rodada de
    encurtar quando o projeto passa para o Modo B depois da tradução."""
    out: dict[str, list[str]] = {}
    for lang in doc.languages:
        if lang == doc.primary_language:
            continue
        ids = over_budget(doc, style, lang, skip_locked=True)
        if ids:
            out[lang] = ids
    return out


# --------------------------------------------------------------------------------------------
# Tempo de cada segmento e aceleração por idioma
# --------------------------------------------------------------------------------------------


@dataclass
class SegmentFit:
    segment_id: str
    slot: float
    """Tempo do segmento no vídeo (s): a narração principal ou o idioma mais longo acelerado ao máximo."""
    durations: dict[str, float]
    tempos: dict[str, float] = field(default_factory=dict)
    """Velocidade (`atempo`) por idioma: acima de 1.0 acelera, abaixo desacelera; 1.0 = sem mudança."""
    pause: float = 0.0
    """Quanto o segmento cresceu além da narração principal (s)."""
    longest: str | None = None
    """Idioma que esticou o segmento (None: coube no tempo da narração principal)."""


def _ceil3(value: float) -> float:
    return math.ceil(value * 1000 - 1e-6) / 1000


def _floor3(value: float) -> float:
    return math.floor(value * 1000 + 1e-6) / 1000


def fit_segments(segment_ids: list[str], durations: dict[str, dict[str, float]], primary: str,
                 max_speedup: float = MAX_SPEEDUP, min_slowdown: float = MIN_SLOWDOWN) -> list[SegmentFit]:
    """Tempo de vaga de cada segmento e a velocidade de cada idioma.

    `durations[idioma][segmento]` é a duração da narração (s). A vaga é a narração principal; um idioma que não cabe
    nela nem acelerado até `max_speedup` estica a vaga (o idioma mais longo define o tempo). O que fica curto é
    desacelerado até `min_slowdown` para ocupar a vaga. A narração principal nunca muda: ela é a referência da
    imagem. Ajuste menor que `TEMPO_STEP` vira 1.0 (o FFmpeg nem aplica).
    """
    fits = []
    for seg_id in segment_ids:
        base = durations[primary][seg_id]
        slot, longest = base, None
        for lang, table in durations.items():
            if lang == primary:
                continue
            need = table[seg_id] / max_speedup
            if need > slot:
                slot, longest = need, lang
        slot = round(slot, 4)
        tempos = {}
        for lang, table in durations.items():
            if lang == primary:
                continue
            ratio = table[seg_id] / slot if slot > 0 else 1.0
            # Arredonda para cima nos dois sentidos: a faixa nunca passa da vaga.
            tempo = min(_ceil3(ratio), _ceil3(max_speedup)) if ratio > 1.0 else max(_ceil3(ratio), _floor3(min_slowdown))
            tempos[lang] = 1.0 if abs(tempo - 1.0) < TEMPO_STEP else tempo
        fits.append(SegmentFit(seg_id, slot, {lang: table[seg_id] for lang, table in durations.items()}, tempos,
                               round(max(0.0, slot - base), 3), longest))
    return fits


def fit_warnings(fits: list[SegmentFit], primary: str, label=lambda code: code) -> list[str]:
    """Avisos para o usuário: trecho que ganhou pausa (tradução longa demais) ou faixa com silêncio longo."""
    out = []
    for index, fit in enumerate(fits, 1):
        if fit.longest and fit.pause > PAUSE_TOLERANCE_S:
            out.append(
                f"Segmento {index}: a narração em {label(fit.longest)} não coube no tempo nem acelerada "
                f"{round((MAX_SPEEDUP - 1) * 100)}%; o vídeo ganhou {fit.pause:.1f} s de pausa neste trecho. Encurte a "
                "tradução para o vídeo voltar ao ritmo original."
            )
        for lang, dur in fit.durations.items():
            if lang == primary:
                continue
            dur = dur / fit.tempos.get(lang, 1.0)  # já com a velocidade ajustada
            gap = fit.slot - dur
            if dur < fit.slot * SHORT_RATIO and gap > SHORT_MIN_GAP_S:
                out.append(
                    f"Segmento {index}: a narração em {label(lang)} ficou curta ({dur:.1f} s de {fit.slot:.1f} s); "
                    "sobra silêncio no fim do trecho. Vale alongar a tradução."
                )
    return out


def fit_summary(fits: list[SegmentFit], primary: str) -> dict[str, Any]:
    """Resumo por idioma para a interface: maior aceleração e desaceleração, trechos ajustados e pausas criadas."""
    langs = sorted({lang for f in fits for lang in f.tempos})
    summary: dict[str, Any] = {"primary": primary, "max_speedup": MAX_SPEEDUP, "min_slowdown": MIN_SLOWDOWN,
                               "added_pause_s": 0.0, "langs": {}}
    summary["added_pause_s"] = round(sum(f.pause for f in fits), 2)
    for lang in langs:
        tempos = [f.tempos[lang] for f in fits]
        summary["langs"][lang] = {
            "max_tempo": max(tempos) if tempos else 1.0,
            "min_tempo": min(tempos) if tempos else 1.0,
            "stretched": sum(1 for t in tempos if t != 1.0),
            "longest_in": sum(1 for f in fits if f.longest == lang),
            "segments": len(fits),
        }
    return summary


def stretch(src: Path, dst: Path, tempo: float) -> Path:
    """Muda a velocidade da narração com `atempo` (sem mudar o tom). O FFmpeg aceita de 0.5 a 100 num filtro só;
    aqui fica em `MIN_SLOWDOWN`–`MAX_SPEEDUP`."""
    if not MIN_SLOWDOWN - 0.01 <= tempo <= MAX_SPEEDUP + 0.01:
        raise ValueError(f"velocidade fora do limite: {tempo:.3f} (de {MIN_SLOWDOWN} a {MAX_SPEEDUP})")
    return to_wav(src, dst, tempo=tempo)


def scale_words(words: list[Word], tempo: float) -> list[Word]:
    """Tempos das palavras depois de mudar a velocidade do áudio por `tempo`."""
    if tempo == 1.0:
        return list(words)
    return [Word(w.text, w.start / tempo, w.end / tempo) for w in words]


# --------------------------------------------------------------------------------------------
# Legendas (closed captions): no Modo B nada é queimado no vídeo
# --------------------------------------------------------------------------------------------

SRT_MAX_CHARS = 42
SRT_MAX_WORDS = 8
_END_PUNCT = (".", "!", "?", "…", "。", "！", "？")


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def caption_lines(words: list[Word], max_chars: int = SRT_MAX_CHARS, max_words: int = SRT_MAX_WORDS) -> list[list[Word]]:
    """Quebra as palavras em linhas de legenda: fim de frase, ou limite de caracteres/palavras."""
    lines: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        text = " ".join(w.text for w in [*current, word])
        if current and (len(text) > max_chars or len(current) >= max_words):
            lines.append(current)
            current = []
        current.append(word)
        if word.text.endswith(_END_PUNCT):
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return lines


def narration_srt(placed: list[tuple[float, float, list[Word]]]) -> str:
    """SRT a partir de (início do segmento, fim da vaga, palavras relativas ao início). Cada linha some quando a
    próxima começa e nunca passa do fim do segmento."""
    entries: list[tuple[float, float, str]] = []
    for start, end_limit, words in placed:
        for line in caption_lines(words):
            a = start + line[0].start
            b = min(start + line[-1].end + 0.3, end_limit)
            text = " ".join(w.text for w in line).strip()
            if text:
                entries.append((a, max(b, a + 0.3), text))
    for i in range(len(entries) - 1):
        a, b, text = entries[i]
        nxt = entries[i + 1][0]
        if b > nxt:
            entries[i] = (a, max(a + 0.1, nxt), text)
    return "\n".join(f"{i}\n{_srt_time(a)} --> {_srt_time(b)}\n{text}\n" for i, (a, b, text) in enumerate(entries, 1))


# --------------------------------------------------------------------------------------------
# Pacote para o YouTube Studio
# --------------------------------------------------------------------------------------------

STUDIO_URL = "https://studio.youtube.com/"
HELP_URL = "https://support.google.com/youtube/answer/13338784"


def _safe_name(lang: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", lang) or "idioma"


PACKAGE_BUSY = ("não deu para gravar o pacote de faixas: feche os arquivos da pasta faixas-youtube (player, editor) e "
                "gere o pacote de novo")


def write_package(
    folder: Path,
    *,
    doc: ProjectDoc,
    entries: dict[str, dict[str, Any]],
    data_dir: Path,
    summary: dict[str, Any] | None = None,
    video_id: str | None = None,
    warnings: list[str] | None = None,
    label=lambda code: code,
) -> dict[str, Any]:
    """Monta a pasta do pacote: uma subpasta por idioma (faixa WAV, legendas SRT, título, descrição e tags) e o
    `CHECKLIST.md` com os passos no YouTube Studio. `entries[idioma]` é a saída do build (caminhos relativos a
    `data_dir`); o idioma principal entra com as legendas e o texto (a faixa dele já está no vídeo). Arquivo aberto
    em outro programa (Windows) faz o `OSError` subir: quem chama troca por `PACKAGE_BUSY`."""
    primary = doc.primary_language
    # Sem apagar a pasta: no Windows, um arquivo aberto (a faixa tocando, o checklist no editor) faria o rmtree
    # falhar. Os arquivos são regravados por cima; subpasta de idioma que saiu do projeto é removida se der.
    folder.mkdir(parents=True, exist_ok=True)
    keep = {_safe_name(lang) for lang in entries}
    for old in folder.iterdir():
        if old.is_dir() and old.name not in keep:
            shutil.rmtree(old, ignore_errors=True)
    langs_out: dict[str, Any] = {}
    missing_meta: list[str] = []
    for lang, entry in entries.items():
        sub = folder / _safe_name(lang)
        sub.mkdir(parents=True, exist_ok=True)
        for stale in ("audio", "legendas"):  # sobras de um pacote anterior (faixa que deixou de existir etc.)
            for old in sub.glob(f"{stale}-*"):
                try:
                    old.unlink()
                except OSError:
                    pass  # aberto: é sobrescrito abaixo, ou fica (não atrapalha o checklist)
        files: dict[str, str] = {}
        if lang != primary and entry.get("mix"):
            src = data_dir / entry["mix"]
            target = sub / f"audio-{_safe_name(lang)}{src.suffix or '.wav'}"
            shutil.copyfile(src, target)
            files["audio"] = target.name
        if entry.get("srt") and (data_dir / entry["srt"]).exists():
            target = sub / f"legendas-{_safe_name(lang)}.srt"
            shutil.copyfile(data_dir / entry["srt"], target)
            files["srt"] = target.name
        meta = doc.metadata.get(lang)
        if meta is not None and meta.title.strip():
            (sub / "titulo.txt").write_text(meta.title.strip()[:100], encoding="utf-8")
            (sub / "descricao.txt").write_text(meta.description.strip(), encoding="utf-8")
            (sub / "tags.txt").write_text(", ".join(meta.tags), encoding="utf-8")
            files.update({"title": "titulo.txt", "description": "descricao.txt", "tags": "tags.txt"})
        else:
            missing_meta.append(lang)
            for name in ("titulo.txt", "descricao.txt", "tags.txt"):
                (sub / name).unlink(missing_ok=True)
        langs_out[lang] = {"folder": sub.name, "files": files, "duration": entry.get("duration")}

    video_link = f"https://studio.youtube.com/video/{video_id}/edit" if video_id else STUDIO_URL
    others = [lang for lang in entries if lang != primary]
    lines = [
        f"# Faixas de idioma: {doc.title}",
        "",
        "O YouTube aceita faixas de áudio dubladas no mesmo vídeo, mas **não pela API**: as faixas vão à mão no "
        "YouTube Studio. Requisito: o canal precisa ter acesso aos **recursos avançados** (verificação do canal).",
        f"Ajuda oficial: {HELP_URL}",
        "",
        "## 1. Vídeo",
        f"- Envie o vídeo em {label(primary)} (pelo app, aba Saída, ou no Studio)." if not video_id
        else f"- O vídeo já está no YouTube: {video_link}",
        "- Ao publicar pelo app, os títulos e descrições traduzidos e as legendas de todos os idiomas vão juntos "
        "pela API (`localizations` e `captions.insert`).",
        "",
        "## 2. Faixa de áudio de cada idioma",
        f"Abra {STUDIO_URL} → **Idiomas** (menu da esquerda) → escolha o vídeo → **Adicionar idioma**.",
    ]
    for step, lang in enumerate(others, 1):
        info = langs_out[lang]
        audio = info["files"].get("audio")
        lines += [
            "",
            f"### 2.{step} {label(lang)} (pasta `{info['folder']}`)",
            f"1. Adicionar idioma → {label(lang)}.",
            f"2. Em **Dublagem**, clique em Adicionar e envie `{audio}`." if audio else "2. (faixa de áudio ausente)",
            "3. Se o YouTube já tiver uma dublagem automática nesse idioma, apague-a antes.",
        ]
        if "title" in info["files"]:
            lines.append("4. Em **Título e descrição**, cole `titulo.txt` e `descricao.txt` (se o app ainda não enviou).")
        if "srt" in info["files"]:
            lines.append(f"5. Em **Legendas**, envie `{info['files']['srt']}` (se o app ainda não enviou).")
        lines.append("6. Clique em Publicar.")
    if primary in langs_out and "srt" in langs_out[primary]["files"]:
        lines += ["", "## 3. Legendas do idioma principal",
                  f"- `{langs_out[primary]['folder']}/{langs_out[primary]['files']['srt']}`: no Modo B as legendas não "
                  "são queimadas no vídeo; envie como legenda (o app faz isso ao publicar)."]
    notes = list(warnings or [])
    if missing_meta:
        notes.append("Sem título traduzido em: " + ", ".join(label(x) for x in missing_meta)
                     + ". Gere os metadados na aba Saída e gere o pacote de novo.")
    if notes:
        lines += ["", "## Atenção", *[f"- {n}" for n in notes]]
    lines += ["", "As faixas têm a mesma duração do vídeo (exigência do YouTube: \"aproximadamente a mesma duração\")."]
    (folder / "CHECKLIST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "primary": primary,
        "video_id": video_id,
        "studio_url": video_link,
        "help_url": HELP_URL,
        "langs": langs_out,
        "summary": summary or {},
        "warnings": notes,
    }
    (folder / "pacote.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
