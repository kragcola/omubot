from __future__ import annotations

from io import StringIO

from loguru import logger

from services.logging_format import escape_loguru_message


def test_channel_format_message_survives_loguru_colorizer() -> None:
    sink = StringIO()
    handler_id = logger.add(
        sink,
        format=lambda record: escape_loguru_message(record["message"]) + "\n",
        colorize=True,
        enqueue=False,
    )
    try:
        message = r"<【信息记录】文字={x} 路径=\\<tag>"
        logger.info(message)
    finally:
        logger.remove(handler_id)

    rendered = sink.getvalue()
    assert "信息记录" in rendered
    assert "文字={x}" in rendered
    assert "路径=" in rendered
