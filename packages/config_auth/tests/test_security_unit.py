from __future__ import annotations

import hashlib
import sys
from pathlib import Path


PACKAGES_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGES_ROOT))

from config_auth.app.security import (  # noqa: E402
    hash_api_key,
    hash_session_token,
    verify_api_key,
)


def test_session_token_hash_is_stable_sha256_hex():
    assert hash_session_token("session-token") == hashlib.sha256(b"session-token").hexdigest()


def test_api_key_hash_uses_supplied_salt_and_verifies_only_matching_secret():
    algorithm, salt_b64, hash_b64 = hash_api_key("sk_test", salt=b"0123456789abcdef")

    assert algorithm == "scrypt"
    assert verify_api_key("sk_test", algorithm, salt_b64, hash_b64) is True
    assert verify_api_key("sk_other", algorithm, salt_b64, hash_b64) is False


def test_api_key_verification_supports_legacy_sha256_hashes():
    legacy_hash = hashlib.sha256(b"legacy-key").hexdigest()

    assert verify_api_key("legacy-key", "sha256", "", legacy_hash) is True
    assert verify_api_key("wrong-key", "sha256", "", legacy_hash) is False
