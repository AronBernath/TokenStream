import hashlib
import hmac
import re
import base64
from typing import Optional

_ID_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")


class AuthError(ValueError):
    pass


def parse_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise AuthError("Missing Authorization header")
    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise AuthError("Invalid Authorization header")
    return parts[1]


def derive_tenant_id(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return digest


def verify_bearer_token_hash(
    token: str, stored_hash: str, *, salt: Optional[str] = None, algorithm: str = "sha256"
) -> bool:
    raw_token = (token or "").strip()
    if not raw_token:
        return False
    algo = (algorithm or "sha256").strip().lower()
    if algo == "scrypt":
        if not salt:
            return False
        try:
            salt_bytes = base64.b64decode(salt.encode("ascii"))
            derived = hashlib.scrypt(
                raw_token.encode("utf-8"),
                salt=salt_bytes,
                n=2**14,
                r=8,
                p=1,
                dklen=32,
            )
            candidate = base64.b64encode(derived).decode("ascii")
        except Exception:
            return False
        return hmac.compare_digest(candidate, stored_hash)

    candidate = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, stored_hash)


def normalize_id(value: str, *, field_name: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ValueError(f"Missing {field_name}")
    if not _ID_RE.match(v):
        raise ValueError(f"Invalid {field_name}: only [a-zA-Z0-9._:-] up to 128 chars")
    return v
