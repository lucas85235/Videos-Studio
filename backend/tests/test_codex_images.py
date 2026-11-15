"""Codex: a imagem certa é a da pasta da nossa sessão, mesmo com outro projeto gerando ao mesmo tempo."""

from __future__ import annotations

import json
import os
import time

from studio.providers.image import codex_new_images, codex_session_ids

OURS = "01a1dd10-aaaa-7000-8000-000000000001"
OTHER = "01a1dd10-bbbb-7000-8000-000000000002"


def _png(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_session_ids_come_from_the_json_events():
    output = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": OURS}),
        json.dumps({"type": "turn.started"}),
        "texto solto com outro id " + OTHER.upper(),
    ])
    assert codex_session_ids(output) == [OURS, OTHER]
    assert codex_session_ids("") == []


def test_picks_our_session_folder_even_if_another_image_is_newer(tmp_path):
    started = time.time()
    ours = _png(tmp_path / OURS / "exec-1.png", started + 1)
    _png(tmp_path / OTHER / "exec-2.png", started + 5)  # outro projeto, mais nova
    output = json.dumps({"type": "thread.started", "thread_id": OURS})
    assert codex_new_images(tmp_path, set(), started, output) == [ours]


def test_without_session_id_falls_back_to_new_files(tmp_path):
    started = time.time()
    old = _png(tmp_path / "antiga" / "exec-0.png", started - 60)
    new = _png(tmp_path / OTHER / "exec-2.png", started + 1)
    assert codex_new_images(tmp_path, {old}, started, "sem json") == [new]
    assert codex_new_images(tmp_path / "nao-existe", set(), started, "") == []


def test_codex_failure_tail_keeps_the_error_lines():
    from studio.providers.image import _codex_tail

    output = "\n".join([
        '{"type":"thread.started"}',
        '{"type":"turn.started"}',
        '{"type":"error","message":"You have hit your usage limit for image generation."}',
        '{"type":"turn.failed","error":{"message":"usage limit"}}',
        "",
    ])
    tail = _codex_tail(output)
    assert "usage limit for image generation" in tail and "thread.started" not in tail
    assert _codex_tail("linha 1\nlinha 2") == "linha 1 | linha 2"  # sem erro marcado: as últimas linhas
    assert len(_codex_tail("x" * 5000)) == 600


def test_codex_usage_limit_is_quota_with_the_retry_time():
    from studio.providers.image import _codex_quota, _codex_quota_message

    output = ('{"type":"error","message":"You\u2019ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), '
              'visit https://chatgpt.com/settings/usage to purchase more credits or try again at 3:43 PM."}')
    assert _codex_quota(output.lower())
    assert _codex_quota_message(output) == "cota de imagens do Codex esgotada (o Codex libera de novo: 3:43 PM)"
    assert _codex_quota('{"error":"usage_limit_reached"}')
    assert _codex_quota_message("usage_limit_reached") == "cota de imagens do Codex esgotada"
    assert not _codex_quota("o codex terminou normalmente")
