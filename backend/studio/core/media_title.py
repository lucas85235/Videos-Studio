"""Título legível a partir do nome de um arquivo de vídeo baixado (ou do título que o yt-dlp devolve).

Os downloaders deixam lixo no nome: o site ("YTDown.com_YouTube_Media_"), o id do vídeo ("-hT4bLj6Jdg_"), um contador
("_001"), a qualidade ("_1080p") e a extensão. `clean_media_title` tira isso e troca `_`/`-` por espaço quando o nome
não tem espaço nenhum (é nome de arquivo). Um título que já é legível passa sem mudança. Usado na referência (envio e
link), no estilo criado a partir dela e no comando `fix-reference-names`; o acervo reaproveita para títulos do yt-dlp.
"""

from __future__ import annotations

import re

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
# Qualidade e contador no fim: " 1080p", "_720p60", "(1080p)", "_hd", " [4K]", "_001".
_QUALITY = re.compile(
    r"(?:(?:[\s_]+|^)\(?\d{3,4}p(?:\d{2})?\)?|_(?:hd|fhd|4k|uhd)|\s*[\[(](?:hd|fhd|4k|uhd)[\])]|_0\d\d)+\s*$",
    re.I,
)


def _looks_like_ytid(token: str) -> bool:
    """Id do YouTube: 11 caracteres com dígito, `-`/`_` ou maiúscula no meio. "Documentary" (uma palavra) não é."""
    return bool(re.search(r"[0-9_-]", token) or re.search(r"[a-z][A-Z]", token))


def clean_media_title(raw: str) -> str:
    """Nome legível a partir do nome de arquivo de downloader (sem site, id, _001, 1080p e extensão).

    Nunca devolve vazio para uma entrada não vazia: se a limpeza comer tudo, volta o texto original (aparado).
    """
    original = (raw or "").strip()
    t = _EXT.sub("", original)
    without_site = _SITE.sub("", t, count=1)
    if without_site != t:
        t = without_site
        head = _YTID_HEAD.match(t)
        if head and _looks_like_ytid(head.group(1)):
            t = t[head.end():]
    tail = _YTID_TAIL.search(t)
    if tail and _looks_like_ytid(tail.group(1)):
        t = t[: tail.start()]
    t = _QUALITY.sub("", t)
    if " " not in t:  # nome de arquivo: `_` e `-` separam as palavras
        t = re.sub(r"[_-]+", " ", t)
    t = " ".join(t.replace("_", " ").split()).strip(" -.")
    return t or original
