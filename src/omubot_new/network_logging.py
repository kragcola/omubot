"""Remove request URLs at HTTPX's log-record boundary, before any handler."""

from __future__ import annotations

import logging
from typing import cast


class _HTTPXRequestURLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX 0.28.1's request INFO record has five positional arguments.
        # Preserve method/protocol/status while dropping the entire URL.
        if record.msg == 'HTTP Request: %s %s "%s %d %s"':
            args = cast(tuple[object, ...], record.args)
            record.args = (args[0], "[redacted URL]", *args[2:])
        return True


_REQUEST_URL_FILTER = _HTTPXRequestURLFilter()


def redact_httpx_request_urls() -> None:
    """Install one stable filter; never change logger levels during a request."""
    logging.getLogger("httpx").addFilter(_REQUEST_URL_FILTER)
