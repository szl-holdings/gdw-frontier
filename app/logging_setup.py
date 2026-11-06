from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .telemetry import LOG_LINES_TOTAL
from .tracing import current_trace_fields

LOGGER_NAME = "gdw"
_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", {})
        if isinstance(fields, dict):
            payload.update(fields)
        payload.update({key: value for key, value in current_trace_fields().items() if key not in payload})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class MetricCountingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        LOG_LINES_TOTAL.labels(level=record.levelname.lower()).inc()


def configure_logging() -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    log_dir = Path(os.getenv("GDW_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()

    file_handler = RotatingFileHandler(
        log_dir / "gdw.log",
        maxBytes=int(os.getenv("GDW_LOG_MAX_BYTES", str(10 * 1024 * 1024))),
        backupCount=int(os.getenv("GDW_LOG_BACKUPS", "5")),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.addHandler(MetricCountingHandler())
    logger.setLevel(os.getenv("GDW_LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    _configured = True
    return logger


def _one_line(value: Any) -> Any:
    """Neutralise CR/LF in request-derived strings so a value can never forge a log record.

    The JSON formatter already escapes newlines, but the sanitiser keeps the guarantee even
    if a plain-text handler is attached (CodeQL py/log-injection).
    """
    if isinstance(value, str):
        return value.replace("\r", " ").replace("\n", " ")
    return value


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, _one_line(message), extra={"fields": {k: _one_line(v) for k, v in fields.items()}})

