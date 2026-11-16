"""Configuração do Vulto (variáveis de ambiente com prefixo VS_)."""

from __future__ import annotations

import os
from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_data_dir() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "VideosStudioData"


class Settings(BaseSettings):
    """Configuração geral. Tudo pode ser sobrescrito por variáveis VS_* ou pelo arquivo .env na raiz."""

    model_config = SettingsConfigDict(
        env_prefix="VS_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=_default_data_dir)
    database_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    node: str = "node"
    renderer_dir: Path = REPO_ROOT / "renderer"
    presets_dir: Path = REPO_ROOT / "presets"
    web_dist_dir: Path = REPO_ROOT / "apps" / "web" / "dist"
    workers_enabled: bool = True
    offline: bool = False
    """Força provedores offline (mock/procedurais), útil para testes e para usar sem internet."""
    render_concurrency: int = 4
    whisper_model: str = "small"
    canais_dir: Path | None = None
    """Pasta do projeto antigo `canais` (importação do histórico). Vazio: `~/canais`."""

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'studio.db').as_posix()}"

    @cached_property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @cached_property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @cached_property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @cached_property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @cached_property
    def library_dir(self) -> Path:
        """Biblioteca de assets do usuário (músicas, SFX, fontes, LUTs)."""
        return self.data_dir / "library"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.outputs_dir, self.uploads_dir, self.tmp_dir, self.library_dir):
            d.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def set_settings(settings: Settings) -> None:
    """Troca a configuração global (usado nos testes)."""
    global _settings
    settings.ensure_dirs()
    _settings = settings
