import sys
from pathlib import Path


SERVICES_ROOT = Path(__file__).resolve().parents[2]
INGESTION_WORKER_ROOT = SERVICES_ROOT / "ingestion_worker"
if str(INGESTION_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(INGESTION_WORKER_ROOT))

from worker import registry_client  # noqa: E402


def test_list_processors_prefers_mounted_snapshot(monkeypatch, tmp_path):
    snapshot = tmp_path / "processors.json"
    snapshot.write_text(
        '{"docs.processor.v1": {"type": "structured_archive", "config": {"include": ["src/**"]}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_client, "PROCESSOR_REGISTRY_PATH", str(snapshot))
    monkeypatch.setattr(registry_client, "PROCESSOR_REGISTRY_URL", "http://unused.internal/processors")

    class FailClient:
        def __init__(self, *, timeout):
            raise AssertionError("API fallback should not be used when mounted processors exist")

    monkeypatch.setattr(registry_client.httpx, "Client", FailClient)

    processors = registry_client.list_processors()

    assert processors == [
        {
            "processor_id": "docs.processor.v1",
            "type": "structured_archive",
            "config": {"include": ["src/**"]},
        }
    ]


def test_list_processors_uses_config_auth_fallback(monkeypatch, tmp_path):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"processor_id": "api.processor.v1", "type": "generic"}]

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == 10.0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, *, headers):
            calls.append((url, headers))
            return FakeResponse()

    monkeypatch.setattr(registry_client, "PROCESSOR_REGISTRY_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr(registry_client, "PROCESSOR_REGISTRY_URL", "http://config-auth.internal/processors")
    monkeypatch.setattr(registry_client, "CONFIG_AUTH_INTERNAL_TOKEN", "secret-token")
    monkeypatch.setattr(registry_client.httpx, "Client", FakeClient)

    assert registry_client.list_processors() == [{"processor_id": "api.processor.v1", "type": "generic"}]
    assert calls == [("http://config-auth.internal/processors", {"Authorization": "Bearer secret-token"})]
