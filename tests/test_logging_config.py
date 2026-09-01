import json
import logging

import pytest

import app.observability.logging_config as logging_config
from app.observability.logging_config import JsonFormatter, configure_logging


def test_json_formatter_produces_valid_json_with_expected_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="gateway.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="something happened: %s",
        args=("detail",),
        exc_info=None,
    )

    parsed = json.loads(formatter.format(record))

    assert parsed["level"] == "WARNING"
    assert parsed["logger"] == "gateway.test"
    assert parsed["message"] == "something happened: detail"


def test_json_formatter_includes_the_traceback():
    """An unhandled exception is the log line most worth having, and the
    traceback is the only part of it that says where. Without this the JSON
    formatter would emit the message and silently drop the stack — exactly
    when structured logging is being relied on to find the cause."""
    try:
        raise ValueError("the actual cause")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="gateway.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["message"] == "request failed"
    assert "ValueError: the actual cause" in parsed["exc_info"]
    assert "Traceback" in parsed["exc_info"]


@pytest.fixture
def _restore_root_logger():
    """configure_logging replaces the root handlers outright, which would
    otherwise leak into every test that runs after these two."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level


@pytest.mark.parametrize(
    "log_format,expected",
    [("json", JsonFormatter), ("text", logging.Formatter)],
)
def test_configure_logging_installs_the_requested_formatter(
    monkeypatch, _restore_root_logger, log_format, expected
):
    monkeypatch.setattr(logging_config.settings, "log_format", log_format)

    configure_logging()

    handler = logging.getLogger().handlers[0]
    assert type(handler.formatter) is expected
