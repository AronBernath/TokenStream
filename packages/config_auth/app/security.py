from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


_PASSWORD_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def needs_password_rehash(password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    return f"sk_{secrets.token_urlsafe(32)}"


def hash_api_key(token: str, *, salt: Optional[bytes] = None) -> tuple[str, str, str]:
    secret_salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        token.encode("utf-8"),
        salt=secret_salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return (
        "scrypt",
        base64.b64encode(secret_salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_api_key(token: str, algorithm: str, salt_b64: str, hash_b64: str) -> bool:
    if algorithm != "scrypt":
        legacy_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy_hash, hash_b64)

    salt = base64.b64decode(salt_b64.encode("ascii"))
    derived = hashlib.scrypt(
        token.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return hmac.compare_digest(base64.b64encode(derived).decode("ascii"), hash_b64)
