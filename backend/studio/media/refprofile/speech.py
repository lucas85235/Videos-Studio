"""Narração da referência (ADR 0004 §3.2, `speech.py`): ritmo da fala, pessoa, tom, estrutura e humor.

- Transcrição com o `faster_whisper` que o app já usa no alinhamento (`providers/align.py`), com a língua DETECTADA
  (a referência pode ser em inglês: forçar "pt" dava texto errado e wpm, pessoa e humor inválidos). A língua usada vai
  para as notas do perfil. Sem o pacote (ou no modo offline), o bloco é pulado e fica `origin: "padrão",
  confidence: 0`.
- Medidas diretas: palavras por minuto (no trecho falado), pausas de 0,35 s ou mais por minuto, perguntas por minuto e a
  pessoa gramatical dominante.
- Tarefa de LLM `reference_humor`: recebe a transcrição e devolve a estrutura (papéis com tempos), as tags de tom e os
  padrões de humor. Regra: descrever o padrão, nunca copiar frases; o filtro anti-cópia vale para padrões, tags de tom
  e papéis da estrutura: o que repete 5 palavras seguidas da transcrição (ou, no texto curto de 3 a 4 palavras, ele
  inteiro) é descartado; o papel copiado vira "bloco".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from studio.config import get_settings

PAUSE_S = 0.35
TRANSCRIPT_MAX_CHARS = 14000
COPY_NGRAM = 5
HUMOR_TASK = "reference_humor"

Word = tuple[str, float, float]  # texto, início, fim

_FIRST = {"eu", "me", "mim", "meu", "minha", "meus", "minhas", "nós", "nos", "nosso", "nossa", "nossos", "nossas",
          "comigo", "conosco", "i", "my", "we", "us", "our"}
_FIRST_PAIRS = {("a", "gente")}
_SECOND = {"você", "vocês", "voce", "voces", "tu", "te", "ti", "teu", "tua", "contigo", "vc", "vcs", "you", "your"}

HUMOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["structure", "tone_tags", "patterns", "gag_count"],
    "properties": {
        "structure": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "start_s", "end_s"],
                "properties": {"role": {"type": "string"}, "start_s": {"type": "number"},
                               "end_s": {"type": "number"}},
            },
        },
        "tone_tags": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "patterns": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
        "gag_count": {"type": "integer"},
    },
}

HUMOR_SYSTEM = (
    "Você analisa a narração de um vídeo de referência do YouTube para que outro canal aprenda a FÓRMULA, nunca o "
    "texto. Devolva: (1) a estrutura em blocos com papel (gancho, contexto, desenvolvimento, revelação, piada, "
    "lista, conclusão, cta) e tempos em segundos; (2) de 3 a 6 tags de tom (ex.: irônico, conversado, urgente, "
    "didático); (3) de 2 a 6 padrões de humor ABSTRATOS, descritos como receita (ex.: 'exagero absurdo logo depois "
    "de um dado sério', 'pergunta retórica respondida com meme'); (4) quantas piadas ou gags há no total. "
    "Regra: descreva o padrão, nunca copie frases. Não cite nomes próprios, bordões nem trechos da transcrição."
)


def available() -> tuple[bool, str]:
    if get_settings().offline:
        return False, "modo offline: a transcrição fica para o modo profundo com o faster-whisper"
    from studio.providers.align import FasterWhisperAligner

    return FasterWhisperAligner().availability()


def transcribe(path: Path, lang: str | None = None) -> tuple[list[Word], str]:
    """Palavras com tempo (faster-whisper local) e a língua usada (a detectada, sem `lang`)."""
    from studio.providers.align import FasterWhisperAligner

    words, language = FasterWhisperAligner().transcribe_detect(path, lang)
    return [(w.text, float(w.start), float(w.end)) for w in words if w.text.strip()], language


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))


def measure(words: list[Word], dur: float) -> dict[str, Any]:
    """Palavras por minuto, pausas e perguntas por minuto e pessoa gramatical dominante."""
    if not words:
        return {}
    spoken = max(1e-6, words[-1][2] - words[0][1])
    gaps = [b[1] - a[2] for a, b in zip(words, words[1:], strict=False)]
    pauses = sum(1 for g in gaps if g >= PAUSE_S)
    questions = sum(1 for w in words if w[0].rstrip().endswith("?"))
    toks = [t for w in words for t in _tokens(w[0])]
    first = sum(1 for t in toks if t in _FIRST) + sum(1 for a, b in zip(toks, toks[1:], strict=False)
                                                      if (a, b) in _FIRST_PAIRS)
    second = sum(1 for t in toks if t in _SECOND)
    person = ""
    if first or second:
        person = "2" if second >= first else "1"
    elif toks:
        person = "3"
    minutes = max(1e-6, dur / 60.0)
    return {"wpm": round(len(words) / (spoken / 60.0), 1), "pauses_per_min": round(pauses / minutes, 2),
            "questions_per_min": round(questions / minutes, 2), "person": person}


def transcript_text(words: list[Word], every_s: float = 10.0) -> str:
    """Transcrição com marcas de tempo a cada ~10 s (para o LLM), cortada em ~14 mil caracteres."""
    parts: list[str] = []
    mark = -every_s
    for text, start, _end in words:
        if start - mark >= every_s:
            parts.append(f"\n[{int(start)}s]")
            mark = start
        parts.append(text)
    out = " ".join(parts).strip()
    return out[:TRANSCRIPT_MAX_CHARS]


def _copies(pattern: str, ngrams: set[tuple[str, ...]], short: set[tuple[str, ...]] | None = None) -> bool:
    """O texto repete 5 palavras seguidas da transcrição (`ngrams`), ou, sendo curto (3 ou 4 palavras), aparece
    inteiro nela (`short`: as sequências de 3 e 4 palavras)."""
    toks = _tokens(pattern)
    if any(tuple(toks[i:i + COPY_NGRAM]) in ngrams for i in range(len(toks) - COPY_NGRAM + 1)):
        return True
    return short is not None and 3 <= len(toks) < COPY_NGRAM and tuple(toks) in short


def default_llm(system: str, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
    """Cadeia de LLM do app na tarefa `reference_humor`."""
    from studio.providers import registry

    chain = registry.resolve_chain("llm", None)
    result, _provider, _ = registry.run_chain(
        "llm", chain,
        lambda p: p.complete_json(task=HUMOR_TASK, system=system, prompt=prompt, schema=HUMOR_SCHEMA,
                                  context=context),
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
    )
    return result.data


def humor(words: list[Word], dur: float,
          llm: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Estrutura, tom e padrões de humor pela tarefa `reference_humor` (sem frases da referência)."""
    if not words:
        return {}
    text = transcript_text(words)
    prompt = (f"Duração do vídeo: {dur:.0f} s.\nTranscrição (com tempos):\n{text}\n\n"
              "Lembre: padrões abstratos, nenhuma frase copiada.")
    data = (llm or default_llm)(HUMOR_SYSTEM, prompt, {"transcript": text, "duration_s": dur, "words": len(words)})
    toks = [t for w in words for t in _tokens(w[0])]
    ngrams = {tuple(toks[i:i + COPY_NGRAM]) for i in range(len(toks) - COPY_NGRAM + 1)}
    short = {tuple(toks[i:i + n]) for n in (3, 4) for i in range(len(toks) - n + 1)}
    patterns = [" ".join(str(p).split())[:200] for p in data.get("patterns") or [] if str(p).strip()]
    patterns = [p for p in patterns if not _copies(p, ngrams, short)][:6]
    structure = []
    for beat in data.get("structure") or []:
        try:
            start = max(0.0, min(dur, float(beat.get("start_s") or 0)))
            end = max(start, min(dur, float(beat.get("end_s") or start)))
        except (TypeError, ValueError):
            continue
        role = " ".join(str(beat.get("role") or "").split())[:40]
        if role and _copies(role, ngrams, short):
            role = "bloco"
        if role:
            structure.append({"role": role, "start_s": round(start, 1), "end_s": round(end, 1)})
    tags = [" ".join(str(t).split())[:40] for t in data.get("tone_tags") or [] if str(t).strip()]
    tags = [t for t in tags if not _copies(t, ngrams, short)][:6]
    try:
        gags = max(0, int(data.get("gag_count") or 0))
    except (TypeError, ValueError):
        gags = 0
    return {"structure": structure, "tone_tags": tags, "patterns": patterns,
            "gag_per_min": round(gags / max(1e-6, dur / 60.0), 2)}
