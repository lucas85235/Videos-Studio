"""Visão: classificar quadros (folhas/tiras de planos) com um esquema JSON (ADR 0004 §3.0).

Interface única `classify_frames(sheets, schema) -> list[dict]`: uma resposta por imagem, com as chaves do esquema.

- `ClaudeVision`: `claude -p` pela conta do usuário, com a ferramenta Read liberada só na pasta temporária onde as
  imagens são copiadas (tarefa `vision_classify` em `providers/llm.py`);
- `CodexVision`: reserva, `codex exec -i` em sandbox só de leitura;
- `MockVision`: determinístico e offline (testes e `VS_OFFLINE=1`), decide pela saturação e pela densidade de bordas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image

from studio.config import get_settings
from studio.providers.llm import VISION_TASK

# Esquema por plano usado na análise da referência (`media/refprofile/vision.py`).
SHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["material", "on_screen_text", "text_case", "text_pos", "font_desc", "has_overlay", "overlay_kind",
                 "is_meme", "mood"],
    "properties": {
        "material": {"type": "string"},
        "on_screen_text": {"type": ["string", "null"]},
        "text_case": {"type": ["string", "null"]},
        "text_pos": {"type": ["string", "null"]},
        "font_desc": {"type": ["string", "null"]},
        "has_overlay": {"type": "boolean"},
        "overlay_kind": {"type": ["string", "null"]},
        "is_meme": {"type": "boolean"},
        "mood": {"type": ["string", "null"]},
    },
}


class VisionProvider(Protocol):
    id: str

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Uma resposta por imagem de `sheets`, na mesma ordem, com as chaves de `schema["properties"]`."""
        ...


def _measure(path: Path) -> tuple[float, float, float]:
    """Saturação média (0 a 1), densidade de bordas (0 a 1) e brilho médio (0 a 1), numa versão de 160 px."""
    with Image.open(path) as img:
        small = img.convert("RGB")
        small.thumbnail((160, 160))
        hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
        gray = np.asarray(small.convert("L"), dtype=np.float32) / 255.0
    saturation = float(hsv[..., 1].mean())
    brightness = float(gray.mean())
    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    edges = float(((gx > 0.12).mean() + (gy > 0.12).mean()) / 2)
    return saturation, edges, brightness


def _empty(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "null" in kinds:
        return None
    if "boolean" in kinds:
        return False
    if "number" in kinds or "integer" in kinds:
        return 0
    if "array" in kinds:
        return []
    if "object" in kinds:
        return {}
    return ""


class MockVision:
    """Classificação determinística por saturação e bordas (testes e `VS_OFFLINE=1`):

    - muitas bordas e pouca cor: `page_capture` (texto de página, notícia);
    - muitas bordas e muita cor: `gameplay`;
    - poucas bordas e pouca cor: `text_card`;
    - muita cor e poucas bordas: `ai_illustration` (superfícies lisas e saturadas);
    - o resto: `photo_real`.
    """

    id = "mock_vision"

    def describe(self, path: Path) -> dict[str, Any]:
        saturation, edges, brightness = _measure(path)
        if edges >= 0.18:
            material = "page_capture" if saturation < 0.18 else "gameplay"
        elif saturation < 0.12:
            material = "text_card"
        elif saturation >= 0.45 and edges < 0.06:
            material = "ai_illustration"
        else:
            material = "photo_real"
        texty = material in ("page_capture", "text_card")
        return {
            "material": material,
            "on_screen_text": "TEXTO" if texty else None,
            "text_case": "upper" if texty else None,
            "text_pos": "center" if texty else None,
            "font_desc": "sans bold" if texty else None,
            "has_overlay": False,
            "overlay_kind": None,
            "is_meme": False,
            "mood": "dark" if brightness < 0.3 else ("bright" if brightness > 0.65 else "neutral"),
            "saturation": round(saturation, 4),
            "edges": round(edges, 4),
        }

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        props: dict[str, Any] = (schema or {}).get("properties") or {}
        out: list[dict[str, Any]] = []
        for sheet in sheets:
            facts = self.describe(Path(sheet))
            if not props:
                out.append(facts)
                continue
            out.append({key: facts[key] if key in facts else _empty(prop) for key, prop in props.items()})
        return out


class VisionUnavailable(RuntimeError):
    """Nenhum provedor de visão real disponível (Claude Code e Codex fora): a análise profunda pula a visão."""


VISION_SYSTEM = (
    "Você classifica planos de um vídeo do YouTube para medir a fórmula de edição. Cada imagem é uma tira com 3 "
    "quadros do mesmo plano (início, meio e fim). Para cada imagem, responda com o índice `i` (a ordem da lista) e:\n"
    "- material: um de gameplay, official_trailer, page_capture (print de página/site), news (manchete, notícia), "
    "meme_reaction (meme, reação, cena de filme/série usada como piada), facecam (rosto do criador), mascot "
    "(avatar/mascote desenhado), photo_real (foto real), stock_video (banco de vídeo genérico), text_card (cartão de "
    "texto, lista), chart_map (gráfico, mapa), ai_illustration (imagem gerada por IA, pintura digital lisa), other;\n"
    "- on_screen_text: o texto principal escrito na tela (até 80 caracteres) ou null; text_case: upper, lower ou "
    "mixed; text_pos: top, center, bottom ou mixed; font_desc: descrição curta da tipografia (ex.: 'sans condensada "
    "pesada com contorno');\n"
    "- has_overlay e overlay_kind (mascot, meme, logo, arrow, circle, highlight, print, other) quando algo está "
    "colado sobre o plano; is_meme; mood (uma palavra).\n"
    "Leia as imagens com a ferramenta Read. Não descreva pessoas reais além do necessário para o material."
)


def _items_schema(schema: dict[str, Any]) -> dict[str, Any]:
    item = {**(schema or SHOT_SCHEMA)}
    props = {"i": {"type": "integer"}, **(item.get("properties") or {})}
    item = {**item, "properties": props, "required": ["i", *(item.get("required") or [])]}
    return {"type": "object", "additionalProperties": False, "required": ["items"],
            "properties": {"items": {"type": "array", "items": item}}}


def _by_index(data: Any, count: int, schema: dict[str, Any]) -> list[dict[str, Any]]:
    """As respostas na ordem das imagens (faltando, o vazio do esquema)."""
    props: dict[str, Any] = (schema or {}).get("properties") or {}
    items = data.get("items") if isinstance(data, dict) else data
    found: dict[int, dict[str, Any]] = {}
    for pos, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("i", pos))
        except (TypeError, ValueError):
            i = pos
        if 0 <= i < count and i not in found:
            found[i] = {k: v for k, v in item.items() if k != "i"}
    out = []
    for i in range(count):
        item = found.get(i)
        if item is None:
            out.append({key: _empty(prop) for key, prop in props.items()} | {"missing": True})
        else:
            out.append({key: item.get(key, _empty(prop)) for key, prop in props.items()})
    return out


def _prompt(names: list[str]) -> str:
    listing = "\n".join(f"{i}: {name}" for i, name in enumerate(names))
    return (f"Imagens (na pasta atual), na ordem:\n{listing}\n\nLeia cada uma com Read e devolva `items` com uma "
            "entrada por imagem, na mesma ordem, com o índice `i`.")


class ClaudeVision:
    """`claude -p` (conta do usuário) com a ferramenta Read liberada só na pasta temporária das imagens (tarefa
    `vision_classify` em `providers/llm.py`)."""

    id = "claude_code_vision"

    def availability(self) -> tuple[bool, str]:
        from studio.providers import registry

        return registry.get("claude_code").availability()

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        from studio.providers import registry

        llm = registry.get("claude_code")
        names = [f"img{n:02d}{Path(p).suffix.lower() or '.jpg'}" for n, p in enumerate(sheets)]
        result = llm.complete_json(task=VISION_TASK, system=VISION_SYSTEM, prompt=_prompt(names),
                                   schema=_items_schema(schema),
                                   context={"images": [str(p) for p in sheets], "names": names})
        return _by_index(result.data, len(sheets), schema)


class CodexVision:
    """Reserva: `codex exec -i` (conta do ChatGPT), em sandbox só de leitura, com a resposta no esquema pedido."""

    id = "codex_vision"
    timeout_s = 600

    def __init__(self, runner: Any = None) -> None:
        self._runner = runner

    def availability(self) -> tuple[bool, str]:
        import shutil

        if not shutil.which("codex"):
            return False, "Codex CLI não encontrado no PATH"
        return True, "ok"

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        import json
        import os
        import shutil
        import subprocess
        import tempfile

        from studio.core.subproc import run_tree
        from studio.providers.base import NotConfigured, ProviderError, TransientError

        codex = shutil.which("codex") or "codex"
        run = self._runner or run_tree
        with tempfile.TemporaryDirectory(prefix="vs-vision-") as workdir:
            names: list[str] = []
            cmd = [codex, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral"]
            # cópia com nome simples: o `-i` separa por vírgula (ver `CodexCLIImage.generate`)
            for n, src in enumerate(sheets):
                name = f"img{n:02d}{Path(src).suffix.lower() or '.jpg'}"
                shutil.copyfile(src, Path(workdir) / name)
                names.append(name)
                cmd += ["-i", str(Path(workdir) / name)]
            schema_file = Path(workdir) / "schema.json"
            schema_file.write_text(json.dumps(_items_schema(schema), ensure_ascii=False), encoding="utf-8")
            last = Path(workdir) / "last.json"
            cmd += ["--output-schema", str(schema_file), "-o", str(last), "--",
                    VISION_SYSTEM + "\n\n" + _prompt(names).replace("Leia cada uma com Read e devolva",
                                                                    "Olhe as imagens anexadas e devolva")]
            env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "CODEX_API_KEY")}
            if not self._runner and not shutil.which("codex"):
                raise NotConfigured("Codex CLI não encontrado")
            try:
                proc = run(cmd, cwd=workdir, env=env, timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                raise TransientError("o Codex demorou demais para classificar os quadros") from exc
            text = last.read_text(encoding="utf-8") if last.is_file() else (getattr(proc, "stdout", "") or "")
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start:end + 1]) if start >= 0 else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            raise ProviderError(f"o Codex não devolveu o JSON da visão: {text.strip()[:200]}")
        return _by_index(data, len(sheets), schema)


class ChainVision:
    """Claude Code e, se ele falhar ou faltar, o Codex. Sem nenhum dos dois: `VisionUnavailable`."""

    id = "chain_vision"

    def __init__(self, providers: list[Any] | None = None) -> None:
        self.providers = providers if providers is not None else [ClaudeVision(), CodexVision()]
        self.used: str | None = None

    def availability(self) -> tuple[bool, str]:
        reasons = []
        for provider in self.providers:
            ok, reason = provider.availability()
            if ok:
                return True, f"{provider.id}: {reason}"
            reasons.append(f"{provider.id}: {reason}")
        return False, "; ".join(reasons) or "nenhum provedor de visão"

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        from studio.providers.base import ProviderError

        errors: list[str] = []
        for provider in self.providers:
            ok, reason = provider.availability()
            if not ok:
                errors.append(f"{provider.id}: {reason}")
                continue
            try:
                out = provider.classify_frames(sheets, schema)
            except ProviderError as exc:
                errors.append(f"{provider.id}: {exc}")
                continue
            self.used = provider.id
            return out
        raise VisionUnavailable("; ".join(errors) or "nenhum provedor de visão")


def get_vision() -> VisionProvider:
    """O provedor de visão em uso: offline o `MockVision`; com rede, o Claude Code com o Codex de reserva."""
    if get_settings().offline:
        return MockVision()
    return ChainVision()


def classify_frames(sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Classifica cada imagem com o provedor em uso (ver `VisionProvider.classify_frames`)."""
    return get_vision().classify_frames(sheets, schema)
