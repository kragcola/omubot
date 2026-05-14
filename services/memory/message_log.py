"""MessageLog compatibility wrapper over ConversationArchive."""

from __future__ import annotations

from services.conversation_archive import ConversationArchive


class MessageLog(ConversationArchive):
    """Persist raw group/private chat messages to SQLite.

    Existing callers still use the historical MessageLog API. The underlying
    implementation now also maintains ConversationArchive tables so future
    scanners can move from repeated recent-window reads to cursor-based reads.
    """
