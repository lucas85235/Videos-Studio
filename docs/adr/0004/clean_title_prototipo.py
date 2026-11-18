import re, sys
_SITE = re.compile(r"^\s*\[?(?:www\.)?(?:ytdown|y2mate|yt1s|ytmp3|ssyoutube|savefrom|snapsave|y2down|x2download|9convert|savetube)(?:\.[a-z]{2,4})?\]?"
                   r"(?:\s*-\s*|_)(?:youtube_)?(?:media_)?", re.I)
_YTID_HEAD = re.compile(r"^[A-Za-z0-9_-]{11}_(?=\S)")
_YTID_TAIL = re.compile(r"\s*[\[(][A-Za-z0-9_-]{11}[\])]\s*$")
_QUALITY = re.compile(r"(?:(?:[\s_]+|^)(?:\(?\d{3,4}p(?:\d{2})?\)?|hd|fhd|4k|uhd)|_\d{1,3})+\s*$", re.I)

def clean_title(raw: str) -> str:
    t = re.sub(r"\.(mp4|mov|mkv|webm|m4v|avi)\s*$", "", raw.strip(), flags=re.I)
    t2 = _SITE.sub("", t, count=1)
    if t2 != t:
        t = _YTID_HEAD.sub("", t2, count=1)
    t = _YTID_TAIL.sub("", t)
    t = _QUALITY.sub("", t)
    if " " not in t:  # nome de arquivo: _ e - viram espaço, menos o hífen entre letra e número (GTA-6 fica "GTA 6" também)
        t = re.sub(r"[_-]+", " ", t)
    t = " ".join(t.replace("_", " ").split()).strip(" -.")
    return t or raw.strip()

out = []
for s in ["YTDown.com_YouTube_Media_-hT4bLj6Jdg_VOCÊ-VAI-ZERAR-GTA-6-PELO-YOUTUBE_001_1080p",
          "Como o GTA 6 vai mudar tudo [dQw4w9WgXcQ]",
          "meu video final", "y2mate.com - Rick Astley Never Gonna Give You Up_1080p",
          "Top-10 jogos 2024", "clip_001", "gameplay_1080p60.mp4", "1080p"]:
    out.append(repr(clean_title(s)))
sys.stdout.buffer.write(("\n".join(out)+"\n").encode("utf-8"))
