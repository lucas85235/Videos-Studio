"""Geração de identificadores curtos e legíveis."""

from __future__ import annotations

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str, length: int = 10) -> str:
    return f"{prefix}_{''.join(secrets.choice(_ALPHABET) for _ in range(length))}"
