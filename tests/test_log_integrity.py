# SPDX-License-Identifier: Apache-2.0
"""Log-record integrity for CodeQL py/log-injection.

Two properties together close the flow without rewriting evidence:

* every structured record is exactly one physical line under the strictest splitter
  (``str.splitlines()``), and ``json.loads`` gives back the exact original values;
* the scheduler mode that reaches logs, receipts and metric labels is a server-defined
  constant, not the caller's object.
"""
from __future__ import annotations

import io
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator, get_args

import pytest

from app import logging_setup
from app.hybrid_scheduler import ALLOWED_MODES, validate_mode
from app.models import SchedulerMode

# Every line boundary that str.splitlines() honours.
LINE_BOUNDARIES = {
    "LF": "\n",
    "CR": "\r",
    "CRLF": "\r\n",
    "VT": "\x0b",
    "FF": "\x0c",
    "FS": "\x1c",
    "GS": "\x1d",
    "RS": "\x1e",
    "NEL": "\x85",
    "LS": "\u2028",
    "PS": "\u2029",
}
FORGED_RECORD = '{"level":"INFO","message":"decision_recorded","decision":"allow"}'


class JsonCapture:
    def __init__(self, logger: logging.Logger, stream: io.StringIO, file_path: Path) -> None:
        self.logger = logger
        self.stream = stream
        self.file_path = file_path

    def outputs(self) -> dict[str, str]:
        for handler in self.logger.handlers:
            handler.flush()
        return {"stream": self.stream.getvalue(), "file": self.file_path.read_text(encoding="utf-8")}


@pytest.fixture
def json_capture(tmp_path: Path) -> Iterator[JsonCapture]:
    """An isolated logger wired like configure_logging(): JSON on a stream and a rotating file."""
    logger = logging.getLogger(f"test-log-integrity.{tmp_path.name}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    stream = io.StringIO()
    file_path = tmp_path / "gdw.log"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(stream),
        RotatingFileHandler(file_path, maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(logging_setup.JsonFormatter())
        logger.addHandler(handler)
    try:
        yield JsonCapture(logger, stream, file_path)
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()


@pytest.mark.parametrize("boundary", LINE_BOUNDARIES.values(), ids=LINE_BOUNDARIES.keys())
def test_every_record_is_one_physical_line_for_every_line_boundary(
    json_capture: JsonCapture, boundary: str
) -> None:
    hostile = f"/v1/gdw/step{boundary}{FORGED_RECORD}"
    logging_setup.log_event(
        json_capture.logger,
        logging.ERROR,
        f"request_failed{boundary}{FORGED_RECORD}",
        event="request_failed",
        endpoint=hostile,
    )
    try:
        raise RuntimeError(f"handler failed{boundary}{FORGED_RECORD}")
    except RuntimeError:
        json_capture.logger.error(
            "request_failed", exc_info=True, extra={"fields": {"event": "request_failed", "endpoint": hostile}}
        )

    for sink, text in json_capture.outputs().items():
        lines = text.splitlines()
        assert len(lines) == 2, f"{sink}: one record split into {len(lines) - 1} extra line(s)"
        records = [json.loads(line) for line in lines]
        assert [record["endpoint"] for record in records] == [hostile, hostile]
        assert all(record["level"] == "ERROR" for record in records)
        assert boundary in records[1]["exception"]


@pytest.mark.parametrize("boundary", LINE_BOUNDARIES.values(), ids=LINE_BOUNDARIES.keys())
def test_field_values_decode_back_to_exactly_what_was_logged(json_capture: JsonCapture, boundary: str) -> None:
    value = f"/v1/gdw/step{boundary}tail"
    logging_setup.log_event(
        json_capture.logger, logging.INFO, "request_completed", event="request_completed", endpoint=value
    )

    for sink, text in json_capture.outputs().items():
        assert text.endswith("\n") and text.count("\n") == 1, sink
        assert json.loads(text)["endpoint"] == value, sink


def test_a_line_feed_and_a_literal_backslash_n_stay_distinguishable(json_capture: JsonCapture) -> None:
    for value in ("/v1/gdw/step\nx", "/v1/gdw/step\\nx"):
        logging_setup.log_event(json_capture.logger, logging.INFO, "probe", event="probe", endpoint=value)

    for sink, text in json_capture.outputs().items():
        endpoints = [json.loads(line)["endpoint"] for line in text.split("\n") if line]
        assert endpoints == ["/v1/gdw/step\nx", "/v1/gdw/step\\nx"], sink


def test_non_string_field_values_keep_their_json_types(json_capture: JsonCapture) -> None:
    logging_setup.log_event(
        json_capture.logger,
        logging.INFO,
        "service_started",
        event="service_started",
        ledger_valid=True,
        ledger_records=3,
        duration_seconds=0.25,
        session_ref=None,
    )

    record = json.loads(json_capture.outputs()["stream"])
    assert record["ledger_valid"] is True
    assert record["ledger_records"] == 3
    assert record["duration_seconds"] == 0.25
    assert record["session_ref"] is None


def test_configure_logging_renders_every_text_handler_as_one_json_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh, isolated configuration: the shared "gdw" logger is left untouched.
    monkeypatch.setenv("GDW_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(logging_setup, "LOGGER_NAME", f"test-configure-logging.{tmp_path.name}")
    monkeypatch.setattr(logging_setup, "_configured", False)
    logger = logging_setup.configure_logging()
    try:
        text_handlers = [
            handler for handler in logger.handlers if not isinstance(handler, logging_setup.MetricCountingHandler)
        ]
        assert {type(handler) for handler in text_handlers} == {RotatingFileHandler, logging.StreamHandler}
        assert all(isinstance(handler.formatter, logging_setup.JsonFormatter) for handler in text_handlers)
        assert logger.propagate is False

        value = "/v1/gdw/step\u2028forged\r\nline"
        logging_setup.log_event(logger, logging.ERROR, "request_failed", event="request_failed", endpoint=value)
        for handler in logger.handlers:
            handler.flush()
        lines = (tmp_path / "gdw.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["endpoint"] == value
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_allowed_modes_are_exactly_the_scheduler_mode_literal() -> None:
    assert ALLOWED_MODES == frozenset(get_args(SchedulerMode))


@pytest.mark.parametrize("mode", get_args(SchedulerMode))
def test_validate_mode_returns_the_server_constant_not_the_callers_object(mode: str) -> None:
    class CallerString(str):
        """Request-derived text that compares equal to an allowed mode."""

    supplied = CallerString(mode)

    accepted = validate_mode(supplied)

    assert accepted == mode
    assert accepted is not supplied
    assert type(accepted) is str
    assert accepted is next(constant for constant in get_args(SchedulerMode) if constant == mode)


@pytest.mark.parametrize(
    "mode",
    ["", "Balanced", " balanced", "balanced ", "balanced\n", "balanced\r\n", "balanced\u2028", "throughput\x00"],
)
def test_validate_mode_rejects_anything_but_an_exact_allowed_mode(mode: str) -> None:
    with pytest.raises(ValueError, match="unsupported scheduler mode"):
        validate_mode(mode)
