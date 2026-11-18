"""Título legível a partir do nome de um arquivo de vídeo baixado ou do título que o yt-dlp devolve.

Dois modos:
- `clean_media_title(raw)` (padrão, para títulos de verdade: yt-dlp, nome digitado): tira só o id do vídeo entre
  colchetes no fim ("Título [dQw4w9WgXcQ]") e a qualidade entre parênteses/colchetes ("(1080p)", "[4K]"). Hífen e
  sublinhado ficam ("Spider-Man", "COVID-19", "Agente_007").
- `clean_media_title(raw, from_filename=True)` (stem do arquivo enviado): também tira o site do downloader
  ("YTDown.com_YouTube_Media_"), o id ("-hT4bLj6Jdg_"), a qualidade ("_1080p"), o contador ("_001", só junto de outro
  marcador de downloader) e a extensão, e troca os separadores por espaço quando o nome não tem espaço.
- `display_title(raw)`: o que mostrar de um título já gravado. Só o nome de downloader (prefixo de site) passa pela
  limpeza de arquivo; o resto, pela leve.

Uma URL ("https://…", o título provisório da referência por link) volta sem mudança nos dois modos.
"""

from __future__ import annotations

import re

_URL = re.compile(r"^\s*https?://", re.I)
_EXT = re.compile(r"\.(?:mp4|mov|mkv|webm|m4v|avi)\s*$", re.I)
# Prefixo de site de download: "YTDown.com_YouTube_Media_", "y2mate.com - ", "[savefrom.net] - "...
_SITE = re.compile(
    r"^\s*\[?(?:www\.)?(?:ytdown|y2mate|yt1s|ytmp3|ssyoutube|savefrom|snapsave|y2down|x2download|9convert|savetube)"
    r"(?:\.[a-z]{2,4})?\]?(?:\s*-\s*|_)(?:youtube_)?(?:media_)?",
    re.I,
)
_YTID = r"[A-Za-z0-9_-]{11}"
_YTID_HEAD = re.compile(rf"^({_YTID})_(?=\S)")  # id logo depois do prefixo do site
_YTID_TAIL = re.compile(rf"\s*[\[(]({_YTID})[\])]\s*$")  # "Título [id]" do yt-dlp
# Qualidade entre parênteses/colchetes no fim ("(1080p)", "[4K]"): vale nos dois modos.
_QUALITY_BRACKETED = re.compile(r"(?:\s*[\[(](?:\d{3,4}p(?:\d{2})?|hd|fhd|4k|uhd)[\])])+\s*$", re.I)
# Qualidade e contador no fim de nome de arquivo: " 1080p", "_720p60", "(1080p)", "_hd", " [4K]", "_001".
_FILE_TAIL = re.compile(
    r"(?:(?:[\s_]+|^)\(?\d{3,4}p(?:\d{2})?\)?|_(?:hd|fhd|4k|uhd)|\s*[\[(](?:hd|fhd|4k|uhd)[\])]|_0\d\d)+\s*$",
    re.I,
)
_QUALITY_MARK = re.compile(r"\d{3,4}p|hd|fhd|4k|uhd", re.I)  # o que faz de "_001" um contador de downloader


def _looks_like_ytid(token: str) -> bool:
    """Id do YouTube: 11 caracteres com dígito, `-`/`_` ou maiúscula no meio. "Documentary" (uma palavra) não é."""
    return bool(re.search(r"[0-9_-]", token) or re.search(r"[a-z][A-Z]", token))


def is_downloader_name(raw: str) -> bool:
    """O texto começa com o prefixo de um site de download ("YTDown.com_YouTube_Media_…", "y2mate.com - …")."""
    return bool(_SITE.match(raw or ""))


def _strip_tail_id(t: str) -> str:
    tail = _YTID_TAIL.search(t)
    return t[: tail.start()] if tail and _looks_like_ytid(tail.group(1)) else t


def clean_media_title(raw: str, *, from_filename: bool = False) -> str:
    """Título legível (ver o módulo). Nunca devolve vazio para uma entrada não vazia: se a limpeza comer tudo, volta o
    texto original (aparado)."""
    original = (raw or "").strip()
    if _URL.match(original):
        return original
    if not from_filename:
        t = _QUALITY_BRACKETED.sub("", _strip_tail_id(original))
        t = _QUALITY_BRACKETED.sub("", _strip_tail_id(t))  # "Título [id] (1080p)" ou "Título (1080p) [id]"
        return " ".join(t.split()).strip() or original
    t = _EXT.sub("", original)
    without_site = _SITE.sub("", t, count=1)
    from_downloader = without_site != t
    if from_downloader:
        t = without_site
        head = _YTID_HEAD.match(t)
        if head and _looks_like_ytid(head.group(1)):
            t = t[head.end():]
    t = _strip_tail_id(t)
    tail = _FILE_TAIL.search(t)
    # "_007" sozinho é parte do título ("Agente_007"); só some junto do site ou de uma qualidade ("_001_1080p").
    if tail and (from_downloader or _QUALITY_MARK.search(tail.group(0))):
        t = t[: tail.start()]
    if " " not in t:  # nome de arquivo: `_` separa as palavras; `-` também, quando há mais de um ("VOCÊ-VAI-ZERAR")
        t = t.replace("_", " ")
        if t.count("-") >= 2:
            t = re.sub(r"-+", " ", t)
    t = " ".join(t.split()).strip(" -.")
    return t or original


def display_title(raw: str) -> str:
    """Título para mostrar/derivar de um título já gravado: limpeza de arquivo só no nome de downloader."""
    return clean_media_title(raw, from_filename=is_downloader_name(raw))
