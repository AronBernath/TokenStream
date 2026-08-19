from __future__ import annotations

import json
import logging
import logging.config
import os
import time
from typing import Any

_CONFIGURED_SERVICE: str | None = None

_TEXT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

_RESERVED_LOG_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


def _utc_timestamp(record: logging.LogRecord) -> str:
    current = time.gmtime(record.created)
    base = time.strftime("%Y-%m-%dT%H:%M:%S", current)
    return f"{base}.{int(record.msecs):03d}Z"


def _resolve_level() -> str:
    name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if not name or not hasattr(logging, name):
        return "INFO"
    return name


def _component_from_logger(logger_name: str, service_name: str) -> str:
    normalized_service = service_name.replace("_", "-")
    normalized_logger = logger_name.replace("_", "-")
    if normalized_logger == normalized_service:
        return "api"
    prefix = f"{normalized_service}."
    if normalized_logger.startswith(prefix):
        return logger_name[len(prefix) :]
    return logger_name


def _event_from_record(record: logging.LogRecord) -> str:
    explicit = getattr(record, "event", None)
    if explicit:
        return str(explicit)
    message = str(record.msg)
    if message and " " not in message and len(message) <= 64:
        return message
    return record.funcName or "log"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


class UTCTextFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return _utc_timestamp(record)


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _utc_timestamp(record),
            "level": record.levelname.lower(),
            "service": self.service_name,
            "component": _component_from_logger(record.name, self.service_name),
            "event": _event_from_record(record),
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key in payload or key == "event":
                continue
            if key.startswith("_") or key in {"color_message"}:
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            exc_type = record.exc_info[0]
            payload["error_type"] = exc_type.__name__ if exc_type else None
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(service_name: str) -> None:
    global _CONFIGURED_SERVICE
    if _CONFIGURED_SERVICE == service_name:
        return
    _CONFIGURED_SERVICE = service_name

    level = _resolve_level()
    log_format = os.environ.get("LOG_FORMAT", "json").strip().lower()
    use_json = log_format not in {"text", "plain", "console"}

    if use_json:
        formatter = {
            "()": JsonLogFormatter,
            "service_name": service_name,
        }
    else:
        formatter = {
            "()": UTCTextFormatter,
            "format": _TEXT_FORMAT,
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": formatter,
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "level": level,
                "handlers": ["console"],
            },
            "loggers": {
                "uvicorn": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "httpx": {
                    "level": os.environ.get("HTTPX_LOG_LEVEL", level).strip().upper() or level,
                    "handlers": ["console"],
                    "propagate": False,
                },
                "httpcore": {
                    "level": os.environ.get("HTTPCORE_LOG_LEVEL", "WARNING").strip().upper() or "WARNING",
                    "handlers": ["console"],
                    "propagate": False,
                },
            },
        }
    )
