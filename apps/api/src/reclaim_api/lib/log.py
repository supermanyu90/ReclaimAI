"""Log redaction helpers and structured logging.

Never log message text, document text, asset fields, identifiers or raw errors.
Helpers return shapes (lengths, hashes, class names), never user content. The rule
is unconditional: dev logs get pasted into issues and shown in demos all the same.
"""

import hashlib
import json
import logging
import sys
from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def message_tag(text: str) -> str:
    """Length and short hash: follow one message through logs without writing it down."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"len={len(text)} sha={sha}"


def error_tag(err: BaseException) -> str:
    """Class name and HTTP status only. Upstream errors routinely echo request bodies."""
    status = getattr(err, "status_code", None) or getattr(err, "status", None)
    if isinstance(status, int):
        return f"error={type(err).__name__} status={status}"
    return f"error={type(err).__name__}"


class JsonFormatter(logging.Formatter):
    """One JSON object per line, shaped for Cloud Logging. Exceptions keep type only."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "correlation_id": correlation_id.get(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["error"] = error_tag(record.exc_info[1])
        http = getattr(record, "http", None)
        if isinstance(http, dict):
            # Cloud Logging's httpRequest shape, from fields chosen by the caller (no URLs or bodies).
            entry["httpRequest"] = {"requestMethod": http.get("method"), "status": http.get("status"),
                                    "latency": f"{http.get('latency_ms', 0) / 1000:.4f}s"}  # fmt: skip
            entry["route"] = http.get("route")
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Access logs include full paths and query strings; keep them out.
    logging.getLogger("uvicorn.access").disabled = True
