"""Provedores de vídeo IA (image-to-video) para as cenas-chave (README §8.5).

Cadeia padrão: Kling 2.5 Turbo Pro → Kling 3 Turbo Standard, os dois pela fila assíncrona do fal.ai. Sem nenhum
disponível, a cena continua com a imagem e o movimento de câmera (Ken Burns), sem gastar nada.

API do fal.ai (conferida em nov/2026 em fal.ai/docs/model-apis/model-endpoints/queue):
  - POST https://queue.fal.run/{modelo} com "Authorization: Key <chave>" e o JSON de entrada → request_id,
    status_url, response_url, cancel_url;
  - GET status_url → IN_QUEUE | IN_PROGRESS | COMPLETED (com `error` quando falhou);
  - GET response_url → {"video": {"url": ...}};
  - PUT cancel_url para desistir (no tempo limite).
A imagem vai como data URI (base64), que os modelos do fal aceitam no `image_url`; o upload para o CDN do fal fica de
fora (só faz falta em arquivos grandes, e o primeiro quadro é recomprimido para JPEG antes).
"""

from __future__ import annotations

import base64
import io
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from studio.config import get_settings
from studio.media.ffmpeg import run_ffmpeg
from studio.providers import credentials
from studio.providers.base import (
    NotConfigured,
    ProviderError,
    ProviderInfo,
    QuotaExceeded,
    Refused,
    TransientError,
    Usage,
    VideoProvider,
    VideoResult,
)

log = logging.getLogger(__name__)

FAL_QUEUE = "https://queue.fal.run"
FAL_KEY_ENV = ("FAL_KEY",)
FAL_VAULT_ID = "fal_kling_turbo"
"""O cofre guarda a chave do fal.ai no primeiro provedor; os outros modelos do fal usam a mesma."""

# Primeiro quadro: lado maior e qualidade do JPEG que vai no data URI (o modelo exige no mínimo 300 px por lado).
FIRST_FRAME_MAX_SIDE = 1536
FIRST_FRAME_MIN_SIDE = 300
POLL_EVERY_S = 5.0
TIMEOUT_S = 15 * 60


@dataclass(frozen=True)
class FalVideoModel:
    """Um modelo image-to-video do fal.ai: endpoint, durações aceitas e preço (página do modelo, nov/2026)."""

    endpoint: str
    durations: tuple[int, ...]
    base_seconds: int
    base_price: float
    per_extra_second: float
    extra_input: tuple[tuple[str, Any], ...] = ()

    def seconds(self, wanted: float) -> int:
        """A duração aceita mais perto da pedida (empate: a menor, que custa menos)."""
        return min(self.durations, key=lambda d: (abs(d - wanted), d))

    def price(self, seconds: float) -> float:
        extra = max(0, self.seconds(seconds) - self.base_seconds)
        base = self.base_price if self.base_seconds else 0.0
        return round(base + extra * self.per_extra_second, 4)


# Kling 2.5 Turbo Pro: 5 s por US$ 0,35 e US$ 0,07 por segundo a mais (só 5 ou 10 s).
KLING_25_TURBO = FalVideoModel(
    endpoint="fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
    durations=(5, 10),
    base_seconds=5,
    base_price=0.35,
    per_extra_second=0.07,
    extra_input=(("negative_prompt", "blur, distort, low quality, text, watermark, logo"),),
)
# Kling 3 Turbo Standard: US$ 0,112 por segundo, de 3 a 15 s.
KLING_3_TURBO = FalVideoModel(
    endpoint="fal-ai/kling-video/v3/turbo/standard/image-to-video",
    durations=tuple(range(3, 16)),
    base_seconds=0,
    base_price=0.0,
    per_extra_second=0.112,
)


def first_frame_data_uri(image: Path) -> str:
    """O primeiro quadro como data URI JPEG (lado maior até FIRST_FRAME_MAX_SIDE, nunca menor que o mínimo)."""
    with Image.open(image) as im:
        im = im.convert("RGB")
        scale = min(1.0, FIRST_FRAME_MAX_SIDE / max(im.size))
        if min(im.size) * scale < FIRST_FRAME_MIN_SIDE:
            scale = FIRST_FRAME_MIN_SIDE / min(im.size)
        if scale != 1.0:
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def looks_like_mp4(path: Path) -> bool:
    """MP4/MOV de verdade (caixa "ftyp" no começo), não uma página de erro salva como vídeo."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("error") or data.get("message") or data
        return str(detail)[:300]
    return str(data)[:300]


def _raise_for(resp: httpx.Response, what: str) -> None:
    """Erro HTTP do fal.ai no erro de provedor certo (a cadeia decide se tenta o próximo)."""
    if resp.status_code < 400:
        return
    text = _error_text(resp)
    lowered = text.lower()
    if resp.status_code in (401, 403) and "balance" not in lowered and "credit" not in lowered:
        raise NotConfigured(f"o fal.ai recusou a chave ({resp.status_code}): confira a chave em Configurações")
    if resp.status_code in (402, 429) or "balance" in lowered or "credit" in lowered:
        raise QuotaExceeded(f"fal.ai sem saldo ou no limite de pedidos ({resp.status_code}): {text}")
    if resp.status_code >= 500:
        raise TransientError(f"fal.ai fora do ar ao {what} ({resp.status_code})")
    if any(word in lowered for word in ("safety", "nsfw", "content policy", "moderation")):
        raise Refused(f"o fal.ai recusou a imagem ou o pedido: {text}")
    raise ProviderError(f"erro do fal.ai ao {what} ({resp.status_code}): {text}")


class FalImageToVideo(VideoProvider):
    """Um modelo image-to-video do fal.ai pela fila assíncrona (enviar, acompanhar, buscar o resultado, baixar)."""

    def __init__(self, info: ProviderInfo, model: FalVideoModel, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, poll_every: float = POLL_EVERY_S,
                 timeout_s: float = TIMEOUT_S) -> None:
        self.info = info
        self.model = model
        self.transport = transport
        """Só nos testes (httpx.MockTransport): nenhum teste chama o fal.ai de verdade."""
        self.sleep = sleep
        self.poll_every = poll_every
        self.timeout_s = timeout_s

    def _key(self) -> str | None:
        own = credentials.resolve(self.info.id, "api_key", FAL_KEY_ENV)
        return own or credentials.resolve(FAL_VAULT_ID, "api_key")

    def availability(self) -> tuple[bool, str]:
        if get_settings().offline:
            return False, "modo offline"
        if not self._key():
            return False, "falta a chave do fal.ai (FAL_KEY ou Configurações)"
        return True, "ok"

    def clip_seconds(self, seconds: float) -> int:
        return self.model.seconds(seconds)

    def price(self, seconds: float) -> float:
        return self.model.price(seconds)

    def _client(self, timeout: float = 60) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout, follow_redirects=True)

    def animate(self, *, image: Path, prompt: str, seconds: int, out_dir: Path) -> VideoResult:
        key = self._key()
        if not key:
            raise NotConfigured("falta a chave do fal.ai")
        seconds = self.model.seconds(seconds)
        body: dict[str, Any] = {"prompt": prompt[:2400], "image_url": first_frame_data_uri(image),
                                "duration": str(seconds), **dict(self.model.extra_input)}
        headers = {"Authorization": f"Key {key}"}
        try:
            with self._client() as client:
                resp = client.post(f"{FAL_QUEUE}/{self.model.endpoint}", json=body, headers=headers)
                _raise_for(resp, "enviar o pedido")
                ticket = resp.json()
                status_url, response_url = ticket.get("status_url"), ticket.get("response_url")
                if not status_url or not response_url:
                    raise TransientError("o fal.ai não devolveu o endereço do pedido")
                data = self._wait(client, headers, status_url, ticket.get("cancel_url"))
                resp = client.get(response_url, headers=headers)
                _raise_for(resp, "buscar o resultado")
                video = (resp.json() or {}).get("video") or {}
                url = video.get("url") if isinstance(video, dict) else None
                if not url:
                    raise TransientError("o fal.ai terminou sem devolver o vídeo")
                out_dir.mkdir(parents=True, exist_ok=True)
                target = out_dir / "clip.mp4"
                with client.stream("GET", url, timeout=300) as stream:
                    if stream.status_code >= 400:
                        stream.read()
                        _raise_for(stream, "baixar o vídeo")
                    with open(target, "wb") as fh:
                        for chunk in stream.iter_bytes():
                            fh.write(chunk)
        except httpx.HTTPError as exc:
            raise TransientError(f"falha de conexão com o fal.ai: {exc}") from exc
        if not looks_like_mp4(target):
            target.unlink(missing_ok=True)
            raise TransientError("o arquivo baixado do fal.ai não é um vídeo MP4")
        cost = self.model.price(seconds)
        meta = {"request_id": ticket.get("request_id"), "inference_s": (data.get("metrics") or {}).get("inference_time")}
        return VideoResult(path=target, seconds=float(seconds), model=self.model.endpoint, meta=meta,
                           usage=Usage(units=float(seconds), unit_name="seconds", cost_usd=cost))

    def _wait(self, client: httpx.Client, headers: dict[str, str], status_url: str, cancel_url: str | None) -> dict[str, Any]:
        """Acompanha o pedido até COMPLETED. No tempo limite, pede o cancelamento e segue a cadeia."""
        started = time.monotonic()
        while True:
            resp = client.get(status_url, headers=headers)
            _raise_for(resp, "acompanhar o pedido")
            data = resp.json() or {}
            status = data.get("status")
            if status == "COMPLETED":
                if data.get("error"):
                    error = str(data.get("error"))
                    kind = str(data.get("error_type") or "")
                    if any(w in (error + kind).lower() for w in ("safety", "nsfw", "content", "moderation")):
                        raise Refused(f"o fal.ai recusou o pedido: {error[:300]}")
                    raise TransientError(f"o fal.ai não conseguiu gerar o vídeo: {error[:300]}")
                return data
            if status not in ("IN_QUEUE", "IN_PROGRESS"):
                raise TransientError(f"situação desconhecida do pedido no fal.ai: {status}")
            if time.monotonic() - started > self.timeout_s:
                if cancel_url:
                    try:
                        client.put(cancel_url, headers=headers)
                    except httpx.HTTPError:  # pragma: no cover - só tenta
                        pass
                raise TransientError(f"o fal.ai passou de {int(self.timeout_s // 60)} min sem terminar o vídeo")
            self.sleep(self.poll_every)


def kling_turbo() -> FalImageToVideo:
    return FalImageToVideo(
        ProviderInfo(
            id="fal_kling_turbo",
            kind="video",
            name="Kling 2.5 Turbo Pro (fal.ai)",
            commercial_use=True,
            credential_fields=("api_key",),
            env_vars=FAL_KEY_ENV,
            notes="Anima a imagem das cenas-chave marcadas. Pago por segundo: o app mostra o custo e pede confirmação "
                  "antes de gerar. A mesma chave serve para os outros modelos do fal.ai.",
            cost_note="US$ 0,35 por clipe de 5 s (US$ 0,70 por 10 s)",
        ),
        KLING_25_TURBO,
    )


def kling_3_turbo() -> FalImageToVideo:
    return FalImageToVideo(
        ProviderInfo(
            id="fal_kling_3",
            kind="video",
            name="Kling 3 Turbo Standard (fal.ai)",
            commercial_use=True,
            env_vars=FAL_KEY_ENV,
            notes="Reserva do Kling 2.5 (aceita de 3 a 15 s). Usa a chave do fal.ai salva no Kling 2.5 Turbo.",
            cost_note="US$ 0,112 por segundo (US$ 0,56 por 5 s)",
        ),
        KLING_3_TURBO,
    )


class MockVideo(VideoProvider):
    """Clipe de teste: a própria imagem parada por alguns segundos (FFmpeg). Só no modo offline."""

    info = ProviderInfo(
        id="mock_video",
        kind="video",
        name="Clipe de teste (imagem parada)",
        commercial_use=False,
        local=True,
        free=True,
        hidden=True,
        notes="Só para testar o pipeline sem chave. Não publique com ele.",
    )

    def clip_seconds(self, seconds: float) -> int:
        return max(1, min(10, round(seconds)))

    def animate(self, *, image: Path, prompt: str, seconds: int, out_dir: Path) -> VideoResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "clip.mp4"
        seconds = self.clip_seconds(seconds)
        run_ffmpeg(["-loop", "1", "-i", str(image), "-t", str(seconds), "-r", "12", "-vf",
                    "scale=trunc(iw/4)*2:trunc(ih/4)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
                   timeout=120)
        return VideoResult(path=target, seconds=float(seconds), model="mock", usage=Usage(units=seconds, unit_name="seconds"))
