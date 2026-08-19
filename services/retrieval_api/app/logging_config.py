from __future__ import annotations

from common.logging_config import configure_logging as configure_service_logging


def configure_logging() -> None:
    configure_service_logging("retrieval-api")
