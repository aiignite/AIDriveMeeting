from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

ENCRYPTED_SECRET_PREFIX = "enc::"
MASKED_SECRET_PLACEHOLDER = "********"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """从 SECRET_KEY 派生 Fernet 密钥。"""
    derived_key = hashlib.sha256(get_settings().SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived_key))


def is_encrypted_secret(value: str | None) -> bool:
    return bool(value and value.startswith(ENCRYPTED_SECRET_PREFIX))


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if is_encrypted_secret(stripped):
        return stripped
    token = _get_fernet().encrypt(stripped.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_SECRET_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if not is_encrypted_secret(value):
        return value
    token = value.removeprefix(ENCRYPTED_SECRET_PREFIX).encode("utf-8")
    try:
        return _get_fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        return None


def mask_secret(value: str | None) -> str | None:
    return MASKED_SECRET_PLACEHOLDER if decrypt_secret(value) else None


def is_masked_secret_placeholder(value: str | None) -> bool:
    return bool(value and value.strip() == MASKED_SECRET_PLACEHOLDER)
