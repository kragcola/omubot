from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bilibili_api import bangumi

from plugins.bilibili import BilibiliPlugin


@pytest.mark.asyncio
async def test_episode_url_resolves_to_bvid_for_video_summary() -> None:
    episode = MagicMock(spec=bangumi.Episode)
    episode.get_bvid = AsyncMock(return_value="BV1xy411c7mD")

    with patch.object(bangumi, "Episode", return_value=episode) as episode_cls:
        resolved = await BilibiliPlugin()._resolve_urls_to_vid(
            "https://www.bilibili.com/bangumi/play/ep123456"
        )

    episode_cls.assert_called_once_with(epid=123456)
    episode.get_bvid.assert_awaited_once_with()
    assert resolved is not None
    assert resolved.key == "BV1xy411c7mD"


@pytest.mark.asyncio
async def test_season_url_resolves_first_episode_aid_for_video_summary() -> None:
    season = MagicMock(spec=bangumi.Bangumi)
    season.get_episode_list = AsyncMock(
        return_value={
            "main_section": {
                "episodes": [
                    {
                        "aid": 987654321,
                        "link": "https://www.bilibili.com/bangumi/play/ep654321",
                    }
                ]
            }
        }
    )

    with patch.object(bangumi, "Bangumi", return_value=season) as bangumi_cls:
        resolved = await BilibiliPlugin()._resolve_urls_to_vid(
            "https://www.bilibili.com/bangumi/play/ss12345"
        )

    bangumi_cls.assert_called_once_with(ssid=12345)
    season.get_episode_list.assert_awaited_once_with()
    assert resolved is not None
    assert resolved.key == "av987654321"
