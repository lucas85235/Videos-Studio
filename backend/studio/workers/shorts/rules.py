"""Regras dos Shorts (avisos de duração e de título), trazidas de `canais/motor/passos/shorts.py`.

Os números são os do canal antigo (diagnóstico de 14/11/2026): alvo de 15 a 30 s, teto de 35 s (45 s para lista e
iceberg), até 50 caracteres antes das hashtags e limite duro de 3 min do YouTube. Cada canal pode trocar os limites em
`Channel.settings["shorts"]` (`duracao_alvo`, `duracao_max`, `duracao_max_lista`, `titulo_max`). Os avisos não travam
nada; só a duração acima de 3 min é erro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from studio.db import session_scope
from studio.models import Channel

TIPOS = ("normal", "lista", "iceberg")
LIST_TIPOS = ("lista", "iceberg")

MAX_S = 180  # limite do YouTube para Shorts (3 min)
DUR_ALVO = (15, 30)  # segundos, com o fim
DUR_MAX = 35
DUR_MAX_LISTA = 45  # "tipo": "lista" ou "iceberg"
TITULO_MAX = 50  # caracteres antes das hashtags

# Fonte no título: "segundo a X" ou nome de veículo (vai para a descrição).
FONTE_TITULO = re.compile(
    r"\bsegundo (a|o|as|os)\b|\b(BBC|Bloomberg|IGN|The Verge|PC Gamer|Kotaku|Eurogamer|Engadget|GamesIndustry|"
    r"Game Developer|VGTimes|IBTimes|Fortune|NBC|Wikipedia|wiki|imprensa)\b",
    re.I,
)
GANCHO_GASTO = re.compile(r"urgente|s[óo] \d+ ?%|\d+ ?% (sabe|acerta)|faltam \d+ dias", re.I)
_HASHTAGS_END = re.compile(r"(\s*#\w+)+\s*$")


class ShortTooLong(ValueError):
    """Duração acima do limite do YouTube para Shorts (erro, não aviso)."""


@dataclass(frozen=True)
class ShortLimits:
    target: tuple[float, float] = DUR_ALVO
    max_s: float = DUR_MAX
    max_list_s: float = DUR_MAX_LISTA
    title_max: int = TITULO_MAX

    def ceiling(self, tipo: str) -> float:
        return self.max_list_s if tipo in LIST_TIPOS else self.max_s


def _num(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out > 0 else default


def limits_from(raw: Any) -> ShortLimits:
    """Limites a partir de `Channel.settings["shorts"]` (chaves do canal.toml); valor ausente ou inválido usa o
    padrão."""
    if not isinstance(raw, dict):
        return ShortLimits()
    target = raw.get("duracao_alvo")
    if isinstance(target, (list, tuple)) and len(target) == 2:
        low, high = _num(target[0], DUR_ALVO[0]), _num(target[1], DUR_ALVO[1])
        target_pair = (low, high) if low <= high else DUR_ALVO
    else:
        target_pair = DUR_ALVO
    return ShortLimits(
        target=target_pair,
        max_s=_num(raw.get("duracao_max"), DUR_MAX),
        max_list_s=_num(raw.get("duracao_max_lista"), DUR_MAX_LISTA),
        title_max=int(_num(raw.get("titulo_max"), TITULO_MAX)),
    )


def channel_rules(channel_id: str | None) -> tuple[ShortLimits, str]:
    """Limites e hashtag principal do canal (sem canal: padrões e sem hashtag)."""
    if not channel_id:
        return ShortLimits(), ""
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return ShortLimits(), ""
        return limits_from((row.settings or {}).get("shorts")), (row.short_hashtag or "").strip()


def _fmt(value: float) -> str:
    """Número do limite como o canal escreveu (35, não 35.0)."""
    return f"{value:g}"


def title_body(title: str) -> str:
    """Título sem o bloco final de hashtags."""
    return _HASHTAGS_END.sub("", title or "").strip()


def check_short(title: str, duration_s: float, tipo: str = "normal", limits: ShortLimits | None = None) -> list[str]:
    """Avisos das regras do Short (duração e título). `duration_s` = segundos do Short, com o fim.

    Acima de 3 min levanta `ShortTooLong` (o YouTube não aceita como Short)."""
    limits = limits or ShortLimits()
    if duration_s > MAX_S:
        raise ShortTooLong(f"O Short tem {duration_s:.0f} s e o YouTube aceita no máximo {MAX_S} s (3 min).")
    warnings: list[str] = []
    lista = tipo in LIST_TIPOS
    teto = limits.ceiling(tipo)
    low, high = limits.target
    if duration_s > teto:
        warnings.append(f"{duration_s:.1f} s passa de {_fmt(teto)} s" + ("" if lista else " (lista e iceberg: escolha o tipo do Short)"))
    elif not lista and not low <= duration_s <= high:
        warnings.append(f"{duration_s:.0f} s fora do alvo de {_fmt(low)} a {_fmt(high)} s")
    body = title_body(title)
    if len(body) > limits.title_max:
        warnings.append(f"título com {len(body)} caracteres antes das hashtags (máximo {limits.title_max})")
    match = FONTE_TITULO.search(body)
    if match:
        warnings.append(f'fonte no título ("{match.group(0)}"): vai para a descrição')
    match = GANCHO_GASTO.search(body)
    if match:
        warnings.append(f'gancho gasto no título ("{match.group(0)}")')
    return warnings


def final_title(title: str, hashtag: str | None) -> str:
    """Título publicado: o título + a hashtag principal do canal, se o bloco final de hashtags não a tem."""
    titulo = (title or "").rstrip()
    hashtag = (hashtag or "").strip()
    match = re.search(r"(\s*#\w+)+$", titulo)
    if not hashtag or (match and hashtag.lower() in match.group(0).lower().split()):
        return titulo
    return f"{titulo} {hashtag}"


def description_hashtags(hashtags: list[str], hashtag: str | None) -> list[str]:
    """Hashtags da descrição sem a hashtag principal (ela já vai no título)."""
    main = (hashtag or "").strip().lower()
    out: list[str] = []
    for tag in hashtags:
        tag = tag.strip()
        if not tag:
            continue
        tag = tag if tag.startswith("#") else f"#{tag}"
        if (main and tag.lower() == main) or tag in out:
            continue
        out.append(tag)
    return out
