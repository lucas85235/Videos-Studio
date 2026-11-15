"""Catálogo do radar de nichos (origem: `catalogo()` de estudio/radar.py e estudio/radar/nichos.toml do canais).

O catálogo editado pelo usuário fica em `AppSetting["radar_catalog"]` (mesmo formato do `nichos.toml`, em JSON); sem
ele, vale o `nichos.toml` empacotado ao lado deste arquivo. O estado do radar (rodar toda semana, última semana, último
job) fica em `AppSetting["radar"]`.
"""

from __future__ import annotations

import copy
import re
import threading
import tomllib
from pathlib import Path
from typing import Any

from studio.db import session_scope
from studio.models import AppSetting

CATALOG_FILE = Path(__file__).with_name("nichos.toml")
CATALOG_KEY = "radar_catalog"
STATE_KEY = "radar"

RADAR_DEFAULTS: dict[str, int] = {
    "orcamento": 3500,  # unidades de cota por coleta (busca = 100, o resto = 1)
    "dias": 30,  # vídeos publicados nos últimos N dias
    "hit_us": 100000,  # views para contar como "hit" nos EUA
    "hit_br": 50000,  # e no Brasil
    "novo_meses": 12,  # canal "novo" = criado há até N meses
    "candidatos": 40,  # canais novos com hit cujos vídeos recentes são lidos (2 unidades cada)
}
DURATIONS = ("medium", "long")  # medium: 4 a 20 min; long: mais de 20 min
ID_RE = re.compile(r"^[a-z0-9-]{1,60}$")
OR_ERROR = "não use | (OR): a API devolve vídeos minúsculos; um termo por linha"

STATE_DEFAULTS: dict[str, Any] = {"auto": False, "last_week": None, "last_job": None}


class CatalogError(ValueError):
    """Catálogo inválido. `errors` traz uma mensagem por problema."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# ----------------------------------------------------------------------------- leitura e validação


def default_catalog() -> dict[str, Any]:
    """O `nichos.toml` empacotado, já normalizado."""
    with open(CATALOG_FILE, "rb") as f:
        return validate_catalog(tomllib.load(f))


def _int(value: Any, name: str, errors: list[str], minimum: int = 0) -> int:
    number: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    if number is None:
        errors.append(f"[radar] {name}: precisa ser um número inteiro")
        return 0
    if number < minimum:
        errors.append(f"[radar] {name}: precisa ser {minimum} ou mais")
    return number


def _pairs(value: Any, name: str, errors: list[str]) -> list[list[str]]:
    """Lista de pares [texto, explicação] (`evitar` e `alertas`)."""
    out: list[list[str]] = []
    if value is None:
        return out
    if not isinstance(value, list):
        errors.append(f"[radar] {name}: precisa ser uma lista de pares [texto, explicação]")
        return out
    for i, item in enumerate(value, 1):
        if not isinstance(item, (list, tuple)) or len(item) != 2 or not all(isinstance(x, str) for x in item):
            errors.append(f"[radar] {name}, item {i}: precisa ser um par [texto, explicação]")
            continue
        a, b = item[0].strip(), item[1].strip()
        if not a:
            errors.append(f"[radar] {name}, item {i}: o texto está vazio")
            continue
        out.append([a, b])
    return out


def _terms(value: Any, where: str, mk: str, errors: list[str]) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        errors.append(f"{where}: os termos '{mk}' precisam ser uma lista")
        return []
    terms = [t.strip() for t in value if isinstance(t, str) and t.strip()]
    if not terms:
        errors.append(f"{where}: falta a busca '{mk}' (pelo menos 1 termo)")
    for t in terms:
        if "|" in t:
            errors.append(f"{where}, termo '{t}': {OR_ERROR}")
    return terms


def validate_catalog(data: Any) -> dict[str, Any]:
    """Confere e normaliza o catálogo. Levanta `CatalogError` com todos os problemas de uma vez."""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise CatalogError(["o catálogo precisa ser um objeto com [radar], [rpm] e [[nichos]]"])
    raw_radar = data.get("radar") or {}
    if not isinstance(raw_radar, dict):
        errors.append("[radar] precisa ser um objeto")
        raw_radar = {}
    radar: dict[str, Any] = {}
    for name, default in RADAR_DEFAULTS.items():
        radar[name] = _int(raw_radar.get(name, default), name, errors, 1 if name != "candidatos" else 0)
    minimo = 2 * radar["candidatos"] + 160  # a reserva (vídeos, canais e recentes) + uma busca
    if radar["orcamento"] and radar["orcamento"] < minimo:
        errors.append(f"[radar] orcamento: precisa ser {minimo} ou mais para caber ao menos uma busca")
    radar["evitar"] = _pairs(raw_radar.get("evitar", []), "evitar", errors)
    radar["alertas"] = _pairs(raw_radar.get("alertas", []), "alertas", errors)

    raw_rpm = data.get("rpm") or {}
    rpm: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_rpm, dict):
        errors.append("[rpm] precisa ser um objeto {categoria: {rpm, pequena}}")
        raw_rpm = {}
    for cat, item in raw_rpm.items():
        if not isinstance(item, dict):
            errors.append(f"[rpm] {cat}: precisa ser {{rpm = número, pequena = sim/não}}")
            continue
        try:
            value = float(item.get("rpm", 0))
        except (TypeError, ValueError):
            errors.append(f"[rpm] {cat}: rpm precisa ser um número")
            continue
        if value < 0:
            errors.append(f"[rpm] {cat}: rpm não pode ser negativo")
        rpm[str(cat)] = {"rpm": value, "pequena": bool(item.get("pequena", False))}

    raw_niches = data.get("nichos") or []
    niches: list[dict[str, Any]] = []
    if not isinstance(raw_niches, list):
        errors.append("[[nichos]] precisa ser uma lista")
        raw_niches = []
    seen: set[str] = set()
    for i, n in enumerate(raw_niches, 1):
        if not isinstance(n, dict):
            errors.append(f"nicho {i}: precisa ser um objeto")
            continue
        nid = str(n.get("id") or "").strip()
        where = f"nicho {nid or i}"
        if not ID_RE.match(nid):
            errors.append(f"{where}: o id precisa ter de 1 a 60 letras minúsculas, números ou hífen")
        elif nid in seen:
            errors.append(f"{where}: id repetido")
        seen.add(nid)
        nome = str(n.get("nome") or "").strip()
        if not nome:
            errors.append(f"{where}: falta o nome")
        cat = str(n.get("cat") or "").strip()
        if not cat:
            errors.append(f"{where}: falta a categoria (cat)")
        duracao = str(n.get("duracao") or "medium").strip()
        if duracao not in DURATIONS:
            errors.append(f"{where}: duracao precisa ser 'medium' (4 a 20 min) ou 'long' (mais de 20 min)")
        niches.append(
            {
                "id": nid,
                "nome": nome,
                "cat": cat,
                "duracao": duracao,
                "en": _terms(n.get("en"), where, "en", errors),
                "pt": _terms(n.get("pt"), where, "pt", errors),
            }
        )
    if errors:
        raise CatalogError(errors)
    return {"radar": radar, "rpm": rpm, "nichos": niches}


# ----------------------------------------------------------------------------- catálogo salvo


def load_catalog() -> dict[str, Any]:
    """O catálogo salvo em `AppSetting["radar_catalog"]` ou, sem ele (ou se ficou inválido), o `nichos.toml`."""
    with session_scope() as s:
        row = s.get(AppSetting, CATALOG_KEY)
        value = copy.deepcopy(row.value) if row and isinstance(row.value, dict) else None
    if value is not None:
        try:
            return validate_catalog(value)
        except CatalogError:
            pass
    return default_catalog()


def is_custom() -> bool:
    with session_scope() as s:
        return s.get(AppSetting, CATALOG_KEY) is not None


def save_catalog(data: Any) -> dict[str, Any]:
    clean = validate_catalog(data)
    with session_scope() as s:
        row = s.get(AppSetting, CATALOG_KEY)
        if row is None:
            s.add(AppSetting(key=CATALOG_KEY, value=clean))
        else:
            row.value = clean
    return clean


def reset_catalog() -> dict[str, Any]:
    """Volta ao `nichos.toml` empacotado (apaga o catálogo editado)."""
    with session_scope() as s:
        row = s.get(AppSetting, CATALOG_KEY)
        if row is not None:
            s.delete(row)
    return default_catalog()


# ----------------------------------------------------------------------------- estado do radar


_state_lock = threading.Lock()


def radar_state() -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(AppSetting, STATE_KEY)
        value = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
    return {**STATE_DEFAULTS, **value}


def update_radar_state(**changes: Any) -> dict[str, Any]:
    """Junta `changes` ao estado salvo (ler-juntar-gravar sob um lock)."""
    with _state_lock, session_scope() as s:
        row = s.get(AppSetting, STATE_KEY)
        current = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
        merged = {**STATE_DEFAULTS, **current, **changes}
        if row is None:
            s.add(AppSetting(key=STATE_KEY, value=merged))
        else:
            row.value = merged
    return merged
