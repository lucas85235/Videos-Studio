"""Provedores de imagem.

Cadeia padrão: Codex CLI (cota do ChatGPT Plus) → procedural (só prévia). Fotos de banco pelo Pexels (opcional).
FLUX (BFL), fal.ai e OpenAI Images (pagos por imagem) foram retirados a pedido do usuário em 14/11/2026.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from studio.core.subproc import run_tree
from studio.providers import credentials
from studio.providers.base import (
    ImageProvider,
    ImageResult,
    NotConfigured,
    ProviderError,
    ProviderInfo,
    QuotaExceeded,
    Refused,
    TransientError,
    Usage,
)

log = logging.getLogger(__name__)


def fit_image(src: Path, dst: Path, width: int, height: int,
              centering: tuple[float, float] = (0.5, 0.5)) -> Path:
    """Recorta/redimensiona para o tamanho exato (preenche e corta em volta de `centering`, padrão o centro)."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        fitted = ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS, centering=centering)
        dst.parent.mkdir(parents=True, exist_ok=True)
        fitted.save(dst, quality=93)
    return dst


def generation_size(width: int, height: int, max_side: int = 1536) -> tuple[int, int]:
    """Tamanho de geração múltiplo de 16 com a mesma proporção (alguns modelos exigem)."""
    scale = min(1.0, max_side / max(width, height))
    w = max(256, int(width * scale) // 16 * 16)
    h = max(256, int(height * scale) // 16 * 16)
    return w, h


_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def codex_session_ids(output: str) -> list[str]:
    """Ids da sessão do `codex exec --json` (o `thread_id` do evento "thread.started" primeiro, depois qualquer
    uuid da saída): o Codex grava a imagem em `generated_images/<id da sessão>/`."""
    ids: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            for key in ("thread_id", "session_id"):
                value = event.get(key)
                if isinstance(value, str) and value not in ids:
                    ids.append(value)
    ids += [u for u in dict.fromkeys(m.lower() for m in _UUID_RE.findall(output)) if u not in ids]
    return ids


def codex_new_images(images_dir: Path, before: set[Path], started: float, output: str) -> list[Path]:
    """As imagens desta chamada do Codex. Outro programa usando o Codex ao mesmo tempo (outro projeto) grava na mesma
    pasta: a imagem certa é a da pasta da nossa sessão. Sem id na saída, vale a mais nova criada durante a chamada."""
    if not images_dir.exists():
        return []
    for session in codex_session_ids(output):
        own = images_dir / session
        if own.is_dir():
            files = [p for p in own.glob("*.png") if p not in before]
            if files:
                return files
    return [p for p in images_dir.rglob("*.png") if p not in before and p.stat().st_mtime >= started - 5]


def _download(url: str, dst: Path, timeout: float = 120) -> Path:
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise TransientError(f"falha ao baixar imagem: {exc}") from exc
    return dst


# --------------------------------------------------------------------------------------------
# Codex CLI (ChatGPT Plus)
# --------------------------------------------------------------------------------------------


_CODEX_QUOTA = ("usage_limit_reached", "usage limit", "rate limit", " 429")
_CODEX_RETRY_RE = re.compile(r"try again (?:at|in) ([^.\"}]{1,40})", re.IGNORECASE)


def _codex_quota(lowered: str) -> bool:
    """A saída do Codex diz que a cota do plano acabou ("You've hit your usage limit", `usage_limit_reached`)."""
    return any(key in lowered for key in _CODEX_QUOTA)


def _codex_quota_message(output: str) -> str:
    """Mensagem da cota esgotada, com a hora em que o Codex diz que volta ("tente de novo: 3:43 PM")."""
    found = _CODEX_RETRY_RE.search(output)
    when = f" (o Codex libera de novo: {found.group(1).strip()})" if found else ""
    return f"cota de imagens do Codex esgotada{when}"


def _codex_tail(output: str, limit: int = 600) -> str:
    """O fim da saída do Codex, numa linha só, para o log: as mensagens de erro (JSON "error"/"message") primeiro."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    errors = [line for line in lines if '"error"' in line or '"type":"turn.failed"' in line or "error:" in line.lower()]
    text = " | ".join((errors or lines)[-4:])
    return text[-limit:]


class CodexCLIImage(ImageProvider):
    info = ProviderInfo(
        id="codex_cli",
        kind="image",
        name="Codex CLI (ChatGPT Plus)",
        commercial_use=True,
        local=True,
        free=True,
        notes="Usa a cota do plano ChatGPT (sem custo extra). Um job por vez; a imagem é detectada na pasta "
        "~/.codex/generated_images. Cota diária não publicada: ao esgotar, a cadeia segue.",
        cost_note="Incluído no ChatGPT Plus; não compre créditos para imagens (custam como a API)",
    )

    timeout_s = 480

    def _codex(self) -> str | None:
        return shutil.which("codex")

    def availability(self) -> tuple[bool, str]:
        if not self._codex():
            return False, "Codex CLI não encontrado no PATH (instale e faça login com o ChatGPT)"
        return True, "ok"

    def generate(self, *, prompt, width, height, seed, out_dir, references=None) -> ImageResult:
        codex = self._codex()
        if not codex:
            raise NotConfigured("Codex CLI não encontrado")
        gen_w, gen_h = generation_size(width, height)
        images_dir = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "generated_images"
        before = {p for p in images_dir.rglob("*.png")} if images_dir.exists() else set()
        instruction = (
            "$imagegen Gere UMA imagem com a ferramenta de geração de imagens e não faça mais nada. "
            f"Tamanho {gen_w}x{gen_h}. Sem nenhum texto, letra, número ou marca d'água na imagem. "
            f"Descrição: {prompt}"
        )
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_API_KEY")}
        cmd = [codex, "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral"]
        for ref in references or []:
            cmd += ["-i", str(ref)]
        cmd.append(instruction)
        started = time.time()
        with tempfile.TemporaryDirectory() as workdir:
            try:
                proc = run_tree(cmd, cwd=workdir, env=env, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise TransientError("o Codex demorou demais para gerar a imagem") from exc
        output = (proc.stdout or "") + (proc.stderr or "")
        lowered = output.lower()
        if _codex_quota(lowered):
            raise QuotaExceeded(_codex_quota_message(output))
        new_files = codex_new_images(images_dir, before, started, proc.stdout or "")
        if not new_files:
            if "refus" in lowered or "can't help" in lowered or "não posso" in lowered:
                raise Refused("o Codex recusou o pedido de imagem")
            # A saída vai para o log: sem ela, "código 1" não diz se foi login, cota, versão ou recusa.
            log.warning("o Codex terminou sem gerar imagem (código %s): %s", proc.returncode, _codex_tail(output))
            raise TransientError(f"o Codex terminou sem gerar imagem (código {proc.returncode})")
        newest = max(new_files, key=lambda p: p.stat().st_mtime)
        final = fit_image(newest, out_dir / "image.jpg", width, height)
        return ImageResult(path=final, usage=Usage(units=1, unit_name="images"), model="codex-imagegen",
                           meta={"source_file": newest.name})


# --------------------------------------------------------------------------------------------
# Pexels (stock)
# --------------------------------------------------------------------------------------------


class PexelsStock(ImageProvider):
    info = ProviderInfo(
        id="pexels",
        kind="image",
        name="Pexels (fotos de banco)",
        commercial_use=True,
        free=True,
        credential_fields=("api_key",),
        env_vars=("PEXELS_API_KEY",),
        cost_note="Grátis (200 pedidos/hora, 20 mil/mês). A API pede link para o Pexels e crédito ao fotógrafo.",
    )

    def availability(self) -> tuple[bool, str]:
        key = credentials.resolve(self.info.id, "api_key", self.info.env_vars)
        return (True, "ok") if key else (False, "sem chave do Pexels")

    def generate(self, *, prompt, width, height, seed, out_dir, references=None) -> ImageResult:
        key = credentials.resolve(self.info.id, "api_key", self.info.env_vars)
        if not key:
            raise NotConfigured("sem chave do Pexels")
        orientation = "portrait" if height > width else ("landscape" if width > height else "square")
        try:
            resp = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": prompt[:120], "orientation": orientation, "per_page": 15},
                headers={"Authorization": key},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise TransientError(f"falha de conexão com o Pexels: {exc}") from exc
        if resp.status_code == 429:
            raise QuotaExceeded("limite do Pexels atingido")
        if resp.status_code != 200:
            raise ProviderError(f"erro do Pexels ({resp.status_code})")
        photos = resp.json().get("photos") or []
        if not photos:
            raise ProviderError(f"nenhuma foto no Pexels para: {prompt[:60]}")
        photo = photos[seed % len(photos)]
        raw = _download(photo["src"].get("original") or photo["src"]["large2x"], out_dir / "raw.jpg")
        final = fit_image(raw, out_dir / "image.jpg", width, height)
        raw.unlink(missing_ok=True)
        license_info = {
            "source": "pexels",
            "url": photo.get("url"),
            "photographer": photo.get("photographer"),
            "photographer_url": photo.get("photographer_url"),
            "license": "Pexels License",
        }
        return ImageResult(path=final, usage=Usage(units=1, unit_name="images"), model="pexels", meta={"license": license_info})


# --------------------------------------------------------------------------------------------
# Procedural (prévia offline)
# --------------------------------------------------------------------------------------------

_PALETTES = [
    ((8, 12, 20), (40, 64, 80), (201, 162, 39)),
    ((12, 8, 6), (70, 42, 22), (232, 176, 74)),
    ((5, 5, 8), (36, 36, 52), (180, 40, 40)),
    ((6, 14, 12), (24, 70, 60), (160, 220, 200)),
    ((14, 10, 18), (60, 40, 80), (230, 200, 255)),
]


class PlaceholderImage(ImageProvider):
    info = ProviderInfo(
        id="placeholder",
        kind="image",
        name="Imagem procedural (prévia)",
        commercial_use=False,
        local=True,
        free=True,
        notes="Arte abstrata gerada localmente para testar o pipeline sem nenhuma chave. Não publique com ela.",
    )

    def generate(self, *, prompt, width, height, seed, out_dir, references=None) -> ImageResult:
        rng = random.Random(seed)
        dark, mid, light = _PALETTES[seed % len(_PALETTES)]
        small_w, small_h = max(64, width // 4), max(64, height // 4)
        img = Image.new("RGB", (small_w, small_h), dark)
        draw = ImageDraw.Draw(img)
        # Gradiente radial com foco aleatório
        cx, cy = rng.uniform(0.25, 0.75) * small_w, rng.uniform(0.25, 0.75) * small_h
        max_r = math.hypot(small_w, small_h)
        for y in range(small_h):
            for x in range(0, small_w, 2):
                d = math.hypot(x - cx, y - cy) / max_r
                t = max(0.0, 1.0 - d * 1.6)
                color = tuple(int(dark[i] + (mid[i] - dark[i]) * t) for i in range(3))
                draw.line([(x, y), (x + 1, y)], fill=color)
        # Formas: silhuetas e brilhos
        for _ in range(rng.randint(3, 7)):
            r = rng.uniform(0.05, 0.25) * min(small_w, small_h)
            x, y = rng.uniform(0, small_w), rng.uniform(0, small_h)
            shade = tuple(min(255, int(c * rng.uniform(0.2, 0.6))) for c in light)
            draw.ellipse([x - r, y - r, x + r, y + r], fill=shade)
        for _ in range(rng.randint(2, 5)):
            x0 = rng.uniform(0, small_w)
            draw.rectangle([x0, small_h * rng.uniform(0.4, 0.8), x0 + rng.uniform(4, small_w * 0.15), small_h], fill=dark)
        img = img.filter(ImageFilter.GaussianBlur(radius=max(1, small_w // 60)))
        img = img.resize((width, height), Image.Resampling.BICUBIC)
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / "image.jpg"
        img.save(final, quality=90)
        return ImageResult(path=final, usage=Usage(units=1, unit_name="images"), model="procedural")
