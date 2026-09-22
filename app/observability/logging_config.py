"""Root logger setup: JSON or text, chosen by LOG_FORMAT.

JSON is for a log pipeline; text is for a terminal. Either way every line
from the request path carries [request_id=...], so a log line and an audit
row and a trace can be lined up.
"""

import json
import logging

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Plays nicely with log aggregators (Loki, CloudWatch, etc.) that expect structured fields rather
    than free text.
    """

    def format(self, record: logging.LogRecord) -> str:
        """One JSON object per line: level, logger, message, timestamp, and the traceback if any."""
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    """Install the JSON or text formatter on the root logger, per LOG_FORMAT."""
    handler = logging.StreamHandler()
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
