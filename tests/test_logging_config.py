import json
import logging

from app.observability.logging_config import JsonFormatter


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
