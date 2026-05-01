import pytest

from services.memory.short_term import ShortTermMemory
from services.memory.timeline import GroupTimeline


@pytest.fixture
def short_term() -> ShortTermMemory:
    return ShortTermMemory()


@pytest.fixture
def group_timeline() -> GroupTimeline:
    return GroupTimeline()
