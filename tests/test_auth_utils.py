import sys
from pathlib import Path

import pytest

_COMMON_ROOT = Path(__file__).resolve().parent.parent / "services" / "common"
sys.path.insert(0, str(_COMMON_ROOT))

from common.auth import AuthError, derive_tenant_id, normalize_id, parse_bearer_token


def test_parse_bearer_token():
    assert parse_bearer_token("Bearer abc123") == "abc123"
    assert parse_bearer_token("bearer token") == "token"
    with pytest.raises(AuthError):
        parse_bearer_token("")
    with pytest.raises(AuthError):
        parse_bearer_token("Token abc")


def test_derive_tenant_id_is_stable():
    assert derive_tenant_id("secret") == derive_tenant_id("secret")
    assert derive_tenant_id("secret") != derive_tenant_id("other")


def test_normalize_id():
    assert normalize_id("resource_123", field_name="resource_id") == "resource_123"
    with pytest.raises(ValueError):
        normalize_id("resource 123", field_name="resource_id")
