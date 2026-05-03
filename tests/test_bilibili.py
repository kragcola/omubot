from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.bilibili import (
    _parse_duration,
    _VideoId,
    evaluate_interest,
    extract_video_id,
    format_video_summary,
    has_bilibili_link,
)

if TYPE_CHECKING:
    from plugins.bilibili import BilibiliPlugin


class TestExtractVideoId:
    def test_bv_in_text(self) -> None:
        vid = extract_video_id("看看这个 BV1xx1234567 视频")
        assert vid is not None
        assert vid.bvid == "BV1xx1234567"
        assert vid.aid is None

    def test_av_in_text(self) -> None:
        vid = extract_video_id("看看 av123456 这个")
        assert vid is not None
        assert vid.aid == 123456
        assert vid.bvid is None

    def test_full_bilibili_url_bv(self) -> None:
        vid = extract_video_id("https://www.bilibili.com/video/BV1xx1234567")
        assert vid is not None
        assert vid.bvid == "BV1xx1234567"

    def test_full_bilibili_url_av(self) -> None:
        vid = extract_video_id("https://www.bilibili.com/video/av123456")
        assert vid is not None
        assert vid.aid == 123456

    def test_no_match(self) -> None:
        assert extract_video_id("普通消息没有链接") is None
        assert extract_video_id("https://youtube.com/watch?v=abc") is None

    def test_bv_first_when_both_present(self) -> None:
        vid = extract_video_id("BV1xx1234567 and av789012")
        assert vid is not None
        assert vid.bvid == "BV1xx1234567"

    def test_video_id_key(self) -> None:
        assert _VideoId(bvid="BV1xx1234567").key == "BV1xx1234567"
        assert _VideoId(aid=123456).key == "av123456"


class TestHasBilibiliLink:
    def test_bv_link(self) -> None:
        assert has_bilibili_link("BV1xx1234567 快看")

    def test_av_link(self) -> None:
        assert has_bilibili_link("av123456 不错")

    def test_b23_link(self) -> None:
        assert has_bilibili_link("https://b23.tv/abc123")

    def test_full_url(self) -> None:
        assert has_bilibili_link("https://www.bilibili.com/video/BV1xx1234567")

    def test_ep_link(self) -> None:
        assert has_bilibili_link("https://www.bilibili.com/bangumi/play/ep123456")

    def test_ss_link(self) -> None:
        assert has_bilibili_link("https://www.bilibili.com/bangumi/play/ss12345")

    def test_no_bilibili_link(self) -> None:
        assert not has_bilibili_link("普通消息")
        assert not has_bilibili_link("https://youtube.com/watch?v=abc")

    def test_empty_text(self) -> None:
        assert not has_bilibili_link("")


class TestFormatVideoSummary:
    def _base_info(self) -> dict:
        return {
            "title": "测试视频",
            "duration": 372,
            "stat": {"view": 12345},
            "desc": "这是一个测试视频简介",
            "tname": "游戏",
            "owner": {"name": "测试UP主"},
        }

    def test_basic_summary(self) -> None:
        result = format_video_summary(self._base_info())
        assert "[B站视频]" in result
        assert "《测试视频》" in result
        assert "时长 6:12" in result
        assert "播放 1.2万" in result
        assert "UP: 测试UP主" in result
        assert "简介: 这是一个测试视频简介" in result
        assert "分区: 游戏" in result

    def test_with_cover_desc(self) -> None:
        result = format_video_summary(self._base_info(), cover_desc="粉色头发的女孩在跳舞")
        assert "封面: 粉色头发的女孩在跳舞" in result

    def test_no_uploader(self) -> None:
        info = self._base_info()
        del info["owner"]
        result = format_video_summary(info)
        assert "UP:" not in result

    def test_no_desc(self) -> None:
        info = self._base_info()
        info["desc"] = ""
        result = format_video_summary(info)
        assert "简介:" not in result

    def test_no_tag(self) -> None:
        info = self._base_info()
        info["tname"] = ""
        result = format_video_summary(info)
        assert "分区:" not in result

    def test_low_view_count(self) -> None:
        info = self._base_info()
        info["stat"]["view"] = 999
        result = format_video_summary(info)
        assert "播放 999" in result

    def test_long_desc_truncated(self) -> None:
        info = self._base_info()
        info["desc"] = "A" * 200
        result = format_video_summary(info)
        desc_part = result.split("简介: ")[1].split(" | ")[0]
        assert len(desc_part) <= 125  # 120 + "…"

    def test_zero_duration(self) -> None:
        info = self._base_info()
        info["duration"] = 0
        result = format_video_summary(info)
        assert "时长 0:00" in result


class TestBilibiliPlugin:
    @pytest.fixture
    def plugin(self) -> BilibiliPlugin:
        from plugins.bilibili import BilibiliPlugin

        p = BilibiliPlugin()
        p._enabled = True
        p._cache_ttl = 3600.0
        p._cover_timeout = 10.0
        p._reply_mode = "mood"
        p._bilibili_talk_value = 0.8
        p._vision_client = None
        p._cache = {}
        return p

    def _make_msg_ctx(self, plain_text: str) -> MagicMock:
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        seg = MessageSegment.text(plain_text)
        msg = Message([seg])
        ctx = MagicMock()
        ctx.raw_message = {
            "plain_text": plain_text,
            "segments": msg,
        }
        ctx.group_id = "123456"
        ctx.user_id = "789"
        return ctx

    @pytest.mark.asyncio
    async def test_no_bilibili_link(self, plugin: BilibiliPlugin) -> None:
        ctx = self._make_msg_ctx("普通消息")
        result = await plugin.on_message(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_disabled_plugin(self, plugin: BilibiliPlugin) -> None:
        plugin._enabled = False
        ctx = self._make_msg_ctx("BV1xx1234567")
        result = await plugin.on_message(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_extract_failure_no_id(self, plugin: BilibiliPlugin) -> None:
        ctx = self._make_msg_ctx("https://b23.tv/abc")
        with patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = "https://bilibili.com/some-page"
            result = await plugin.on_message(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_api_failure(self, plugin: BilibiliPlugin) -> None:
        ctx = self._make_msg_ctx("看看 BV1xx1234567")
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.side_effect = Exception("API error")
            result = await plugin.on_message(ctx)
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_injection(self, plugin: BilibiliPlugin) -> None:
        ctx = self._make_msg_ctx("看看 https://www.bilibili.com/video/BV1xx1234567")
        fake_info = {
            "title": "测试视频",
            "duration": 372,
            "pic": "",
            "stat": {"view": 50000},
            "desc": "测试简介",
            "tname": "游戏",
            "owner": {"name": "UP主"},
        }
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            result = await plugin.on_message(ctx)
        assert result is False
        # Verify summary was injected into segments
        segments = ctx.raw_message["segments"]
        text_data = segments[0].data["text"]
        assert "[B站视频]" in text_data
        assert "《测试视频》" in text_data

    @pytest.mark.asyncio
    async def test_injection_with_cover(self, plugin: BilibiliPlugin) -> None:
        ctx = self._make_msg_ctx("BV1xx1234567")
        fake_info = {
            "title": "测试视频",
            "duration": 100,
            "pic": "https://example.com/cover.jpg",
            "stat": {"view": 1000},
            "desc": "",
            "tname": "",
            "owner": {},
        }
        plugin._vision_client = MagicMock()
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
            patch.object(plugin, "_describe_cover", new_callable=AsyncMock) as mock_cover,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            mock_cover.return_value = "一个可爱的动漫女孩"
            result = await plugin.on_message(ctx)
        assert result is False
        segments = ctx.raw_message["segments"]
        assert "封面: 一个可爱的动漫女孩" in segments[0].data["text"]

    @pytest.mark.asyncio
    async def test_cover_failure_not_fatal(self, plugin: BilibiliPlugin) -> None:
        ctx = self._make_msg_ctx("BV1xx1234567")
        fake_info = {
            "title": "测试视频",
            "duration": 100,
            "pic": "https://example.com/cover.jpg",
            "stat": {"view": 1000},
            "desc": "",
            "tname": "",
            "owner": {},
        }
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
            patch.object(plugin, "_describe_cover", new_callable=AsyncMock) as mock_cover,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            mock_cover.side_effect = Exception("Network error")
            result = await plugin.on_message(ctx)
        assert result is False
        # Still got the basic summary
        assert "《测试视频》" in ctx.raw_message["segments"][0].data["text"]
        assert "封面:" not in ctx.raw_message["segments"][0].data["text"]

    @pytest.mark.asyncio
    async def test_cache_used(self, plugin: BilibiliPlugin) -> None:
        """Verify cached video info is reused within TTL."""
        vid = _VideoId(bvid="BV1xx1234567")
        fake_info = {"title": "缓存视频", "duration": 60, "stat": {"view": 100}, "desc": "", "tname": "", "owner": {}}

        # First call: populate cache
        with patch("bilibili_api.video.Video") as mock_video_cls:
            mock_v = MagicMock()
            mock_v.get_info = AsyncMock(return_value=fake_info)
            mock_video_cls.return_value = mock_v

            info1 = await plugin._get_video_info(vid)
            assert info1 == fake_info
            assert mock_video_cls.call_count == 1

            # Second call: should use cache (API not called again)
            info2 = await plugin._get_video_info(vid)
            assert info2 == fake_info
            assert mock_video_cls.call_count == 1  # no new API call

    @pytest.mark.asyncio
    async def test_resolve_b23_no_links(self, plugin: BilibiliPlugin) -> None:
        result = await plugin._resolve_b23_links("普通消息")
        assert result == "普通消息"

    def test_inject_summary_no_segments(self, plugin: BilibiliPlugin) -> None:
        ctx = MagicMock()
        ctx.raw_message = {}
        plugin._inject_summary(ctx, "summary")  # should not raise

    def test_collect_segment_text_from_json(self, plugin: BilibiliPlugin) -> None:
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        json_data = (
            '{"ver":"1.0.0.19","prompt":"[QQ小程序]【PJSK】测试视频",'
            '"meta":{"detail_1":"https://www.bilibili.com/video/BV1xx1234567"}}'
        )
        seg = MessageSegment.json(json_data)
        msg = Message([seg])
        ctx = MagicMock()
        ctx.raw_message = {"plain_text": "", "segments": msg}
        result = plugin._collect_segment_text(ctx)
        assert "BV1xx1234567" in result
        assert "测试视频" in result

    def test_collect_segment_text_plain_only(self, plugin: BilibiliPlugin) -> None:
        ctx = MagicMock()
        ctx.raw_message = {"plain_text": "看看 BV1xx1234567", "segments": None}
        result = plugin._collect_segment_text(ctx)
        assert "BV1xx1234567" in result

    @pytest.mark.asyncio
    async def test_json_card_detection(self, plugin: BilibiliPlugin) -> None:
        """B站 link inside a JSON mini-program card should be detected."""
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        json_data = (
            '{"prompt":"[QQ小程序]测试","meta":{"url":"https://b23.tv/abc123"}}'
        )
        seg = MessageSegment.json(json_data)
        msg = Message([seg])
        ctx = MagicMock()
        ctx.raw_message = {"plain_text": "", "segments": msg}
        ctx.group_id = "123456"
        ctx.user_id = "789"

        with (
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
        ):
            mock_resolve.return_value = "https://www.bilibili.com/video/BV1xx9999999"
            mock_get.return_value = {
                "title": "JSON卡片视频", "duration": 60, "pic": "",
                "stat": {"view": 100}, "desc": "", "tname": "", "owner": {},
            }
            result = await plugin.on_message(ctx)
        assert result is False
        # plain_text should be updated
        assert "JSON卡片视频" in ctx.raw_message["plain_text"]

    def test_extract_json_info_bilibili_card(self, plugin: BilibiliPlugin) -> None:
        """Extract B站 metadata from a QQ mini-program JSON card."""
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        json_data = (
            '{"prompt":"[QQ小程序]测试","meta":{"detail_1":'
            '{"appid":"1109937557","desc":"测试视频标题","preview":"https://example.com/cover.jpg"}}}'
        )
        seg = MessageSegment.json(json_data)
        msg = Message([seg])
        ctx = MagicMock()
        ctx.raw_message = {"plain_text": "", "segments": msg}
        result = plugin._extract_bilibili_json_info(ctx)
        assert result is not None
        assert result["title"] == "测试视频标题"
        assert result["preview"] == "https://example.com/cover.jpg"

    def test_extract_json_info_non_bilibili(self, plugin: BilibiliPlugin) -> None:
        """Non-B站 mini-program cards should return None."""
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        json_data = '{"prompt":"[QQ小程序]其他","meta":{"detail_1":{"appid":"999999","desc":"非B站"}}}'
        seg = MessageSegment.json(json_data)
        msg = Message([seg])
        ctx = MagicMock()
        ctx.raw_message = {"plain_text": "", "segments": msg}
        result = plugin._extract_bilibili_json_info(ctx)
        assert result is None


class TestParseDuration:
    def test_mm_ss(self) -> None:
        assert _parse_duration("6:12") == 372

    def test_hh_mm_ss(self) -> None:
        assert _parse_duration("1:02:05") == 3725

    def test_zero(self) -> None:
        assert _parse_duration("0:00") == 0

    def test_empty_returns_zero(self) -> None:
        assert _parse_duration("") == 0


class TestEvaluateInterest:
    def test_high_interest_video(self) -> None:
        """Project Sekai WxS video should score high."""
        score = evaluate_interest("【プロセカ】Wonderlands×Showtime 新曲MV「Smile Symphony」")
        assert score > 0.5

    def test_medium_interest_video(self) -> None:
        """Anime cover song should score medium."""
        score = evaluate_interest("【翻唱】可爱音游主题曲 cover 唱见")
        assert 0.2 < score < 0.75

    def test_low_interest_video(self) -> None:
        """Generic gaming video should score low."""
        score = evaluate_interest("【游戏实况】暗区突围 PvP 日常")
        assert 0.05 <= score < 0.3

    def test_no_match_video_gets_floor(self) -> None:
        """Video with no matching keywords gets the floor."""
        score = evaluate_interest("今日新闻 天气预报 交通状况")
        assert score == 0.05

    def test_score_capped_at_one(self) -> None:
        """Score never exceeds 1.0 even with many keywords."""
        score = evaluate_interest(
            "Project Sekai プロセカ PJSK 世界计划 Wonderlands×Showtime WxS "
            "天马司 草薙宁宁 神代类 凤笑梦 Emu 凤凰 乐园 舞台 演出 smile "
            "Vocaloid 初音未来 Miku 镜音 巡音"
        )
        assert score == 1.0

    def test_case_insensitive(self) -> None:
        """Keyword matching is case-insensitive."""
        lower = evaluate_interest("project sekai miku wonderful show")
        upper = evaluate_interest("PROJECT SEKAI MIKU WONDERFUL SHOW")
        assert lower == upper
        assert lower > 0.3

    def test_emu_name_triggers_interest(self) -> None:
        """Video title containing the bot's name should score high."""
        score = evaluate_interest("凤笑梦的舞台表演合集")
        assert score >= 0.3  # 0.05 floor + 0.25 for 凤笑梦


class TestBilibiliReplyHint:
    @pytest.fixture
    def plugin(self) -> BilibiliPlugin:
        from plugins.bilibili import BilibiliPlugin

        p = BilibiliPlugin()
        p._enabled = True
        p._cache_ttl = 3600.0
        p._cover_timeout = 10.0
        p._reply_mode = "always"
        p._bilibili_talk_value = 0.8
        p._vision_client = None
        p._cache = {}
        return p

    def _make_msg_ctx(self, plain_text: str) -> MagicMock:
        from nonebot.adapters.onebot.v11 import Message, MessageSegment

        seg = MessageSegment.text(plain_text)
        msg = Message([seg])
        ctx = MagicMock()
        ctx.raw_message = {
            "plain_text": plain_text,
            "segments": msg,
        }
        ctx.group_id = "123456"
        ctx.user_id = "789"
        return ctx

    @pytest.mark.asyncio
    async def test_hint_set_for_always_mode(self, plugin: BilibiliPlugin) -> None:
        """When reply_mode='always', raw_message gets _bilibili_reply hint."""
        ctx = self._make_msg_ctx("BV1xx1234567")
        fake_info = {
            "title": "测试视频", "duration": 60, "pic": "",
            "stat": {"view": 100}, "desc": "", "tname": "", "owner": {},
        }
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            await plugin.on_message(ctx)
        hint = ctx.raw_message.get("_bilibili_reply")
        assert hint is not None
        assert hint["mode"] == "always"
        assert hint["bilibili_talk_value"] == 0.8

    @pytest.mark.asyncio
    async def test_no_hint_for_mood_mode(self, plugin: BilibiliPlugin) -> None:
        """When reply_mode='mood', no hint is set (backward compatible)."""
        plugin._reply_mode = "mood"
        ctx = self._make_msg_ctx("BV1xx1234567")
        fake_info = {
            "title": "测试视频", "duration": 60, "pic": "",
            "stat": {"view": 100}, "desc": "", "tname": "", "owner": {},
        }
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            await plugin.on_message(ctx)
        assert "_bilibili_reply" not in ctx.raw_message

    @pytest.mark.asyncio
    async def test_hint_includes_interest_for_autonomous(self, plugin: BilibiliPlugin) -> None:
        """When reply_mode='autonomous', hint includes interest_score."""
        plugin._reply_mode = "autonomous"
        ctx = self._make_msg_ctx("BV1xx1234567")
        fake_info = {
            "title": "【Project Sekai】新曲MV", "duration": 60, "pic": "",
            "stat": {"view": 100}, "desc": "", "tname": "", "owner": {},
        }
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            await plugin.on_message(ctx)
        hint = ctx.raw_message.get("_bilibili_reply")
        assert hint is not None
        assert hint["mode"] == "autonomous"
        assert "interest_score" in hint
        assert 0.0 <= hint["interest_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_dedicated_hint_no_interest_score(self, plugin: BilibiliPlugin) -> None:
        """When reply_mode='dedicated', hint does not include interest_score."""
        plugin._reply_mode = "dedicated"
        ctx = self._make_msg_ctx("BV1xx1234567")
        fake_info = {
            "title": "测试视频", "duration": 60, "pic": "",
            "stat": {"view": 100}, "desc": "", "tname": "", "owner": {},
        }
        with (
            patch.object(plugin, "_get_video_info", new_callable=AsyncMock) as mock_get,
            patch.object(plugin, "_resolve_b23_links", new_callable=AsyncMock) as mock_resolve,
        ):
            mock_resolve.return_value = ctx.raw_message["plain_text"]
            mock_get.return_value = fake_info
            await plugin.on_message(ctx)
        hint = ctx.raw_message.get("_bilibili_reply")
        assert hint is not None
        assert hint["mode"] == "dedicated"
        assert "interest_score" not in hint
