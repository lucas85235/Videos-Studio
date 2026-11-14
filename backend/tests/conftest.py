from __future__ import annotations

from pathlib import Path

import pytest

from studio.config import REPO_ROOT, Settings, set_settings
from studio.db import init_db, reset_engine


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path):
    """Cada teste usa um diretório de dados e um banco próprios, com provedores offline."""
    settings = Settings(
        data_dir=tmp_path / "data",
        presets_dir=REPO_ROOT / "presets",
        offline=True,
        workers_enabled=False,
    )
    set_settings(settings)
    reset_engine()
    init_db()
    yield settings
    reset_engine()
