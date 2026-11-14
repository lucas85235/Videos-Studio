"""Cofre de credenciais: segredos criptografados (Fernet) no banco, chave mestra no Windows Credential Manager.

Também aceita variáveis de ambiente (ex.: GEMINI_API_KEY), que têm prioridade e são úteis em desenvolvimento.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from studio.config import get_settings
from studio.db import session_scope
from studio.models import ProviderCredential

log = logging.getLogger(__name__)

_SERVICE = "VideosStudio"
_KEY_NAME = "master-key"
_lock = threading.Lock()
_fernet: Fernet | None = None


def _load_master_key() -> bytes:
    """Lê/cria a chave mestra. Prefere o keyring; cai para um arquivo no diretório de dados."""
    settings = get_settings()
    if not settings.offline:
        try:
            import keyring

            stored = keyring.get_password(_SERVICE, _KEY_NAME)
            if stored:
                return stored.encode("ascii")
            key = Fernet.generate_key()
            keyring.set_password(_SERVICE, _KEY_NAME, key.decode("ascii"))
            return key
        except Exception as exc:  # noqa: BLE001 - keyring indisponível
            log.warning("keyring indisponível (%s); usando arquivo de chave no diretório de dados", exc)
    path = settings.data_dir / ".master.key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.write_bytes(key)
    return key


def _cipher() -> Fernet:
    global _fernet
    with _lock:
        if _fernet is None:
            _fernet = Fernet(_load_master_key())
        return _fernet


def reset_cipher() -> None:
    global _fernet
    with _lock:
        _fernet = None


def set_credential(provider: str, fields: dict[str, str]) -> None:
    clean = {k: v.strip() for k, v in fields.items() if v is not None and str(v).strip()}
    token = _cipher().encrypt(json.dumps(clean).encode("utf-8")).decode("ascii")
    with session_scope() as s:
        row = s.get(ProviderCredential, provider)
        if row is None:
            s.add(ProviderCredential(provider=provider, secret=token))
        else:
            row.secret = token


def delete_credential(provider: str) -> None:
    with session_scope() as s:
        row = s.get(ProviderCredential, provider)
        if row is not None:
            s.delete(row)


def get_credential(provider: str) -> dict[str, str]:
    with session_scope() as s:
        row = s.get(ProviderCredential, provider)
        token = row.secret if row else None
    if not token:
        return {}
    try:
        return json.loads(_cipher().decrypt(token.encode("ascii")))
    except (InvalidToken, ValueError):
        log.error("não foi possível descriptografar a credencial de %s", provider)
        return {}


def resolve(provider: str, field: str, env_vars: tuple[str, ...] = ()) -> str | None:
    """Valor de um campo: variável de ambiente (se houver) ou cofre."""
    for name in env_vars:
        value = os.environ.get(name)
        if value:
            return value
    return get_credential(provider).get(field) or None


def configured_fields(provider: str) -> list[str]:
    return sorted(get_credential(provider).keys())


def mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def describe(provider: str, fields: tuple[str, ...], env_vars: tuple[str, ...]) -> dict[str, Any]:
    stored = get_credential(provider)
    env = next((name for name in env_vars if os.environ.get(name)), None)
    return {
        "fields": {f: mask(stored.get(f)) for f in fields},
        "from_env": env,
        "configured": bool(env) or all(stored.get(f) for f in fields) if fields else True,
    }
