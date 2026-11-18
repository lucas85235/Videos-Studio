"""Projeto antigo de referência para a compatibilidade dos contratos (ADR 0004 §3.0).

`fixtures/projeto_antigo.json` guarda um `ProjectDoc` gravado ANTES dos campos novos da fundação (sem `shots`, `fx`,
`mark`, `tier`...) e os hashes dos blocos de render que ele gerava. `timeline_hashes` remonta a timeline do mesmo
jeito; o teste compara com o gravado. Para regravar (só quando uma mudança de timeline for intencional):

    cd backend && uv run python tests/compat_fixture.py --write
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "projeto_antigo.json"
STYLES = ("misterios-short", "curiosidades-longo")
RENDERER_VERSION = "compat-v1"
IMAGE_KINDS = ("image", "video")


def _clips(doc: Any) -> dict[str, Any]:
    """Narração sintética: palavras do roteiro espaçadas por igual, 0,32 s cada."""
    from studio.core.timeline import NarrationClip
    from studio.providers.base import Word

    clips = {}
    for seg in doc.segments:
        tokens = seg.text_for(doc.primary_language).split()
        step = 0.32
        clips[seg.id] = NarrationClip(segment_id=seg.id, path=f"cache/tts/{seg.id}.wav", duration=len(tokens) * step + 0.2,
                                      words=[Word(w, i * step, i * step + step * 0.9) for i, w in enumerate(tokens)])
    return clips


def timeline_hashes(doc_data: dict[str, Any], style_id: str) -> dict[str, Any]:
    """A timeline do projeto no estilo: hash dela inteira e de cada bloco de render."""
    from studio.core.hashing import hash_obj
    from studio.core.project.document import ProjectDoc
    from studio.core.styles.library import get_style
    from studio.core.timeline import build_timeline, chunk_timeline

    doc = ProjectDoc.model_validate(doc_data)
    style = get_style(style_id)
    images = {s.id: f"cache/images/{s.id}.jpg" for s in doc.scenes if s.kind in IMAGE_KINDS}
    timeline = build_timeline(doc, style, doc.primary_language, _clips(doc), images)
    chunks = chunk_timeline(timeline, RENDERER_VERSION)
    return {"timeline": hash_obj(timeline), "chunks": [c["key"] for c in chunks]}


def _legacy_doc() -> dict[str, Any]:
    """O documento antigo: imagem de IA, reaproveitamento, cartões, mídia real (foto, notícia, oficial) e sugestão."""
    seg_texts = [
        ("hook", "Em 2013 um jogo vendeu 475 milhões de cópias e ninguém esperava isso."),
        ("body", "A cidade inteira foi desenhada a partir de fotos reais de Los Angeles e de Miami."),
        ("chapter", "Capítulo dois: o segredo do mapa que ninguém percebeu na primeira vez."),
        ("body", "Os jogadores acharam 3 cidades escondidas e uma delas tinha 12 prédios iguais."),
        ("cta", "Se você gostou, se inscreva e conta nos comentários qual é o próximo jogo."),
    ]
    segments = [{"id": f"seg_old{i}", "role": role, "text": {"pt-BR": text}} for i, (role, text) in enumerate(seg_texts)]
    media_photo = {"kind": "photo", "source": "commons", "asset": "uploads/prj_old/media/0123456789abcdef.jpg",
                   "url": "https://upload.wikimedia.org/x.jpg", "page_url": "https://commons.wikimedia.org/wiki/File:X.jpg",
                   "title": "Los Angeles", "author": "Fulano", "license": "CC BY-SA 4.0", "credit": "Fulano / Commons",
                   "width": 1920, "height": 1080, "focus": "top_left"}
    media_news = {"kind": "news", "source": "link", "asset": "uploads/prj_old/media/fedcba9876543210.jpg",
                  "page_url": "https://news.example.com/a", "title": "Jogo bate recorde", "outlet": "Jornal Exemplo",
                  "published": "2013-09-20", "headline": {"pt-BR": "Jogo bate recorde de vendas"}, "editorial": True,
                  "width": 1280, "height": 720}
    media_official = {"kind": "official", "source": "link", "asset": "uploads/prj_old/media/00112233445566aa.jpg",
                      "page_url": "https://www.rockstargames.com/", "credit": "Rockstar Games", "editorial": True,
                      "width": 1920, "height": 1080}
    scenes: list[dict[str, Any]] = [
        {"id": "scn_o00", "segment_id": "seg_old0", "part": 0, "parts": 2, "kind": "image", "span": 7,
         "visual": {"prompt": "a crowded game store at night", "language": "editorial", "shot": "wide"},
         "motion": "ken_burns_in", "card": {"headline": {"pt-BR": "475 MILHÕES"}}},
        {"id": "scn_o01", "segment_id": "seg_old0", "part": 1, "parts": 2, "kind": "stat", "span": 6,
         "visual": {"type": "none", "reuse_of": "scn_o00"},
         "card": {"value": {"pt-BR": "475 MI"}, "label": {"pt-BR": "cópias vendidas"}, "source": "Take-Two"}},
        {"id": "scn_o10", "segment_id": "seg_old1", "part": 0, "parts": 3, "kind": "image", "span": 6,
         "visual": {"prompt": "fallback", "media": media_photo}},
        {"id": "scn_o11", "segment_id": "seg_old1", "part": 1, "parts": 3, "kind": "image", "span": 5,
         "visual": {"prompt": "fallback", "media": media_news}},
        {"id": "scn_o12", "segment_id": "seg_old1", "part": 2, "parts": 3, "kind": "image", "span": 4,
         "visual": {"prompt": "a skyline at dusk", "reuse_of": "scn_o00"}, "focus": "right", "motion": "zoom_to",
         "real": {"kind": "photo", "query": "miami skyline", "note": "Foto real de Miami"}},
        {"id": "scn_o20", "segment_id": "seg_old2", "part": 0, "parts": 1, "kind": "title",
         "visual": {"type": "none"}, "overlay_text": {"pt-BR": "O SEGREDO DO MAPA"}},
        {"id": "scn_o30", "segment_id": "seg_old3", "part": 0, "parts": 2, "kind": "image", "span": 7,
         "visual": {"prompt": "fallback", "media": media_official}, "transition_in": "flash",
         "look": {"brightness": 0.1, "saturation": -0.2}},
        {"id": "scn_o31", "segment_id": "seg_old3", "part": 1, "parts": 2, "kind": "compare", "span": 6,
         "visual": {"type": "none", "reuse_of": "scn_o30"},
         "card": {"items": [{"label": {"pt-BR": "cidades"}, "value": {"pt-BR": "3"}},
                            {"label": {"pt-BR": "prédios iguais"}, "value": {"pt-BR": "12"}}], "highlight": 1}},
        {"id": "scn_o40", "segment_id": "seg_old4", "part": 0, "parts": 1, "kind": "image",
         "visual": {"prompt": "x", "reuse_of": "scn_o00"}, "card": {"stamp": {"pt-BR": "INSCREVA-SE"}}},
    ]
    return {"id": "prj_old", "title": "Projeto antigo", "style_id": "misterios-short", "seed": 7,
            "segments": segments, "scenes": scenes}


def write() -> None:
    from studio.core.project.document import ProjectDoc

    doc = ProjectDoc.model_validate(_legacy_doc()).model_dump(mode="json")
    out = {"doc": doc, "expected": {style_id: timeline_hashes(doc, style_id) for style_id in STYLES}}
    FIXTURE.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"gravado {FIXTURE}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        # A biblioteca de estilos lê os presets e o banco da configuração: dados num diretório temporário.
        import tempfile

        from studio.config import REPO_ROOT, Settings, set_settings
        from studio.db import init_db, reset_engine

        tmp = Path(tempfile.mkdtemp(prefix="vulto-compat-"))
        set_settings(Settings(data_dir=tmp, presets_dir=REPO_ROOT / "presets", offline=True, workers_enabled=False))
        reset_engine()
        init_db()
        write()
        reset_engine()
