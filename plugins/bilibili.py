"""BilibiliPlugin: B 站视频链接识别 — 拉取标题/封面/简介/标签注入消息上下文。

Pipeline interceptor (priority=190), runs after ElementDetector (210).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import unescape as _html_unescape

import httpx
from loguru import logger
from pydantic import BaseModel

from kernel.types import AmadeusPlugin, MessageContext, PluginContext

_log = logger.bind(channel="bilibili")

# URL patterns
_BV_PATTERN = re.compile(r"(?:BV|bv)([0-9A-Za-z]{10})")
_AV_PATTERN = re.compile(r"(?:av|AV)(\d+)")
_B23_PATTERN = re.compile(r"b23\.tv/([A-Za-z0-9]+)")
_BILIBILI_VIDEO_PATTERN = re.compile(r"bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)")
_EP_PATTERN = re.compile(r"bilibili\.com/bangumi/play/(ep\d+|ss\d+)")

_VIDEO_INFO_PROMPT = (
    "用一句简短的中文描述这张B站视频封面图：画面内容、人物、动作、氛围。"
    "要像真人随手发的截图说明，20字以内。"
)

# Threshold below which keyword-based interest triggers LLM fallback.
_INTEREST_LLM_FALLBACK = 0.2

_INTEREST_LLM_PROMPT = (
    "你是鳳笑梦（Emu Otori），一个乐园偶像。"
    "你对这个视频有多感兴趣？先只输出一个0到100的整数（不要任何其他文字），然后换行再解释。"
)


class BilibiliConfig(BaseModel):
    enabled: bool = True
    cache_ttl: float = 3600.0
    cover_timeout: float = 10.0
    reply_mode: str = "mood"  # "mood" | "always" | "dedicated" | "autonomous"
    bilibili_talk_value: float = 0.8  # base probability for dedicated/autonomous modes


@dataclass
class _VideoCacheEntry:
    info: dict
    stored_at: float


@dataclass
class _VideoId:
    """Parsed B站 video identifier: either a bvid (BV...) or aid (int)."""

    bvid: str | None = None
    aid: int | None = None

    @property
    def key(self) -> str:
        if self.bvid:
            return self.bvid
        return f"av{self.aid}"


def extract_video_id(text: str) -> _VideoId | None:
    """Extract a Bilibili video ID from text. Returns _VideoId or None."""
    m = _BV_PATTERN.search(text)
    if m:
        return _VideoId(bvid=f"BV{m.group(1)}")
    m = _AV_PATTERN.search(text)
    if m:
        return _VideoId(aid=int(m.group(1)))
    m = _BILIBILI_VIDEO_PATTERN.search(text)
    if m:
        raw = m.group(1)
        if raw.startswith("BV"):
            return _VideoId(bvid=raw)
        if raw.startswith("av"):
            return _VideoId(aid=int(raw[2:]))
    return None


def has_bilibili_link(text: str) -> bool:
    """Check if text contains any B站 link."""
    if _BV_PATTERN.search(text):
        return True
    if _AV_PATTERN.search(text):
        return True
    if _B23_PATTERN.search(text):
        return True
    if _BILIBILI_VIDEO_PATTERN.search(text):
        return True
    return bool(_EP_PATTERN.search(text))


def _parse_duration(raw: str) -> int:
    """Convert 'MM:SS' or 'HH:MM:SS' to total seconds."""
    parts = raw.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities from text."""
    return _html_unescape(_HTML_TAG.sub("", text))


# === Interest evaluation for autonomous reply mode ===
# Keywords matched case-insensitively against video titles.
# Weights are additive, capped at 1.0, with a 0.05 floor.

_HIGH_INTEREST: list[str] = [
    "project sekai", "プロセカ", "pjsk", "世界计划",
    "wonderlands", "wxs",
    "天马司", "草薙宁宁", "神代类", "凤笑梦", "emu", "otori",
    "凤凰", "乐园", "游乐园", "奇幻乐园",
    "舞台", "演出", "show", "live",
    "笑容", "笑顔", "smile",
    "vocaloid", "初音未来", "miku", "镜音", "巡音", "kaito", "meiko",
]
_MEDIUM_INTEREST: list[str] = [
    "音游", "音乐游戏", "rhythm game", "节奏游戏",
    "翻唱", "唱见", "cover", "歌ってみた",
    "偶像", "idol",
    "可爱", "kawaii", "萌",
    "鲷鱼烧", "甜食", "甜品", "甜点",
    "元气", "活力",
    "杂技", "acrobatics",
    "动画", "动漫", "anime",
    "日本", "japan",
]
_LOW_INTEREST: list[str] = [
    "游戏", "game", "ゲーム",
    "二次元",
    "冒险", "探险",
    "快乐", "开心", "happy",
    "音乐", "music", "歌",
    "有趣", "好玩",
    "新作", "新曲", "新歌",
    "派对", "party",
]


def evaluate_interest(title: str) -> float:
    """Compute a 0.0-1.0 interest score for a video title against the bot's persona."""
    title_lower = title.lower()
    score = 0.05  # floor — even unrecognized videos have a tiny baseline
    for kw in _HIGH_INTEREST:
        if kw in title_lower:
            score += 0.25
    for kw in _MEDIUM_INTEREST:
        if kw in title_lower:
            score += 0.12
    for kw in _LOW_INTEREST:
        if kw in title_lower:
            score += 0.05
    return min(score, 1.0)


def format_video_summary(info: dict, cover_desc: str | None = None) -> str:
    """Build a human-readable video summary block from bilibili API response."""
    title = _strip_html(info.get("title", "未知标题"))
    duration_s = info.get("duration", 0)
    minutes = duration_s // 60
    seconds = duration_s % 60
    duration_str = f"{minutes}:{seconds:02d}"

    stat = info.get("stat", {})
    view_count = stat.get("view", 0)
    view_str = f"{view_count / 10000:.1f}万" if view_count >= 10000 else str(view_count)

    desc = info.get("desc", "")
    if desc:
        desc = desc[:120].replace("\n", " ")
        if len(info.get("desc", "")) > 120:
            desc += "…"

    tag_text = ""
    tname = info.get("tname", "")
    if tname:
        tag_text = f"分区: {tname}"

    owner = info.get("owner", {})
    uploader = owner.get("name", "")

    parts: list[str] = ["[B站视频]", f"《{title}》"]
    if uploader:
        parts.append(f"UP: {uploader}")
    parts.append(f"时长 {duration_str} | 播放 {view_str}")
    if desc:
        parts.append(f"简介: {desc}")
    if tag_text:
        parts.append(tag_text)
    if cover_desc:
        parts.append(f"封面: {cover_desc}")

    return " | ".join(parts)


class BilibiliPlugin(AmadeusPlugin):
    name = "bilibili"
    description = "B站视频链接识别：拉取标题/封面/简介/标签，注入消息上下文"
    version = "1.1.0"
    priority = 190

    async def on_startup(self, ctx: PluginContext) -> None:
        from kernel.config import load_plugin_config

        cfg = load_plugin_config("plugins/bilibili.toml", BilibiliConfig)
        self._enabled = cfg.enabled
        self._cache_ttl = cfg.cache_ttl
        self._cover_timeout = cfg.cover_timeout
        self._reply_mode = cfg.reply_mode
        self._bilibili_talk_value = cfg.bilibili_talk_value
        self._vision_client = getattr(ctx, "vision_client", None)
        self._llm_client = getattr(ctx, "llm_client", None)
        self._cache: dict[str, _VideoCacheEntry] = {}

        if self._enabled:
            _log.info("bilibili plugin enabled | cache_ttl={}s cover_timeout={}s reply_mode={}",
                      self._cache_ttl, self._cover_timeout, self._reply_mode)
        else:
            _log.info("bilibili plugin disabled")

    async def on_message(self, ctx: MessageContext) -> bool:
        if not self._enabled:
            return False

        # Collect text from ALL segment types (text, json mini-program cards, etc.)
        combined_text = self._collect_segment_text(ctx)

        vid = None
        info = None

        # Try direct URL extraction from text
        if combined_text and has_bilibili_link(combined_text):
            resolved_text = await self._resolve_b23_links(combined_text)
            vid = extract_video_id(resolved_text)

        # Try JSON mini-program card (QQ shares B站 as mini-program without URL)
        if vid is None:
            json_info = self._extract_bilibili_json_info(ctx)
            if json_info:
                info = await self._search_video(json_info["title"])
                if info:
                    _log.info(
                        "bilibili | json card resolved | title={!r} -> bvid={}",
                        json_info["title"], info.get("bvid", "?"),
                    )

        if vid is None and info is None:
            return False

        vid_key = vid.key if vid else info.get("bvid", "search")

        # Fetch video info by ID if not already found via search
        if vid is not None and info is None:
            _log.info("bilibili | group={} user={} vid={}", ctx.group_id, ctx.user_id, vid_key)
            try:
                info = await self._get_video_info(vid)
            except Exception:
                _log.warning("bilibili | vid={} video info fetch failed", vid_key)
                return False

        if not info:
            return False

        title = info.get("title", "")
        _log.info("bilibili | vid={} title={!r}", vid_key, title[:60])

        # Download cover → vision describe (non-blocking, failure is OK)
        cover_desc = None
        pic_url = info.get("pic", "")
        if pic_url and self._vision_client:
            try:
                cover_desc = await self._describe_cover(pic_url)
            except Exception:
                _log.debug("bilibili | vid={} cover description failed", vid_key)

        # Build summary and inject into message segments
        summary = format_video_summary(info, cover_desc)
        self._inject_summary(ctx, summary)

        # Also update plain_text so the rest of the pipeline sees non-empty content.
        # Preserve original text so user's words alongside a video card aren't lost.
        original_text = ctx.raw_message.get("plain_text", "")
        if original_text:
            ctx.raw_message["plain_text"] = f"{original_text}\n{summary}"
        else:
            ctx.raw_message["plain_text"] = summary

        _log.info("bilibili | vid={} summary injected ({} chars)", vid_key, len(summary))

        # Set reply hint for scheduler (skip when mode is "mood" — no behavior change)
        if self._reply_mode != "mood":
            hint: dict[str, object] = {
                "mode": self._reply_mode,
                "bilibili_talk_value": self._bilibili_talk_value,
                "video_title": title,
            }
            if self._reply_mode == "autonomous":
                interest = evaluate_interest(title)
                if interest < _INTEREST_LLM_FALLBACK and self._llm_client:
                    llm_score = await self._evaluate_interest_llm(title)
                    if llm_score is not None:
                        _log.info(
                            "bilibili | vid={} keyword={:.2f} llm={:.2f} title={!r}",
                            vid_key, interest, llm_score, title[:60],
                        )
                        interest = llm_score
                    else:
                        _log.info("bilibili | vid={} interest={:.2f} (keyword only, LLM failed) title={!r}",
                                  vid_key, interest, title[:60])
                else:
                    _log.info("bilibili | vid={} interest={:.2f} title={!r}", vid_key, interest, title[:60])
                hint["interest_score"] = interest
            ctx.raw_message["_bilibili_reply"] = hint

        return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_segment_text(ctx: MessageContext) -> str:
        """Collect searchable text from all message segments, including JSON cards."""
        parts: list[str] = []
        plain_text = ctx.raw_message.get("plain_text", "")
        if plain_text:
            parts.append(plain_text)

        segments = ctx.raw_message.get("segments")
        if segments is None:
            return "".join(parts)

        for seg in segments:
            seg_type = getattr(seg, "type", "")
            seg_data = getattr(seg, "data", {}) or {}
            if seg_type == "json" and isinstance(seg_data, dict):
                raw = seg_data.get("data", "")
                if isinstance(raw, str):
                    _log.debug("bilibili | json seg data={}", raw[:500])
                    parts.append(raw)
            elif seg_type == "forward" and isinstance(seg_data, dict):
                raw = seg_data.get("id", "")
                if raw:
                    parts.append(raw)

        return "".join(parts)

    @staticmethod
    def _extract_bilibili_json_info(ctx: MessageContext) -> dict | None:
        """Extract B站 video metadata from a QQ mini-program JSON card.

        QQ mini-program cards for B站 contain metadata (title, cover preview)
        but NOT the video URL. Returns a dict with title/search query info,
        or None if this isn't a B站 mini-program card.
        """
        import json

        segments = ctx.raw_message.get("segments")
        if segments is None:
            return None

        for seg in segments:
            if getattr(seg, "type", "") != "json":
                continue
            seg_data = getattr(seg, "data", {}) or {}
            raw = seg_data.get("data", "")
            if not isinstance(raw, str):
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            meta = data.get("meta", {})
            detail = meta.get("detail_1", {}) if isinstance(meta, dict) else {}

            # Check if this is a B站 mini-program card
            appid = str(detail.get("appid", "")) if isinstance(detail, dict) else ""
            if appid != "1109937557":
                continue

            title = detail.get("desc", "") or detail.get("title", "")
            if isinstance(title, str) and title:
                _log.debug("bilibili | json card bilibili appid={} title={!r}", appid, title)
                return {"title": title, "preview": detail.get("preview", "")}

        return None

    async def _resolve_b23_links(self, text: str) -> str:
        """Follow b23.tv redirects and return text with resolved URLs."""
        matches = list(_B23_PATTERN.finditer(text))
        if not matches:
            return text

        result = text
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            for m in matches:
                short_url = f"https://b23.tv/{m.group(1)}"
                try:
                    resp = await client.get(short_url)
                    final_url = str(resp.url)
                    _log.debug("bilibili | b23.tv resolved: {} -> {}", short_url, final_url)
                    result = result.replace(m.group(0), final_url)
                except Exception:
                    _log.debug("bilibili | b23.tv resolve failed: {}", short_url)
        return result

    async def _get_video_info(self, vid: _VideoId) -> dict | None:
        """Fetch video info from bilibili API, with local cache."""
        now = time.monotonic()
        vid_key = vid.key
        entry = self._cache.get(vid_key)
        if entry and (now - entry.stored_at) < self._cache_ttl:
            _log.debug("bilibili | cache HIT vid={}", vid_key)
            return entry.info

        from bilibili_api import video

        try:
            v = video.Video(bvid=vid.bvid) if vid.bvid else video.Video(aid=vid.aid)
            info = await v.get_info()
        except Exception:
            _log.warning("bilibili | API call failed vid={}", vid_key)
            return None

        if info:
            self._cache[vid_key] = _VideoCacheEntry(info=info, stored_at=now)

        return info

    async def _search_video(self, keyword: str) -> dict | None:
        """Search B站 for a video by title keyword. Returns the first result, cached."""
        cache_key = f"__search__{keyword}"
        now = time.monotonic()
        entry = self._cache.get(cache_key)
        if entry and (now - entry.stored_at) < self._cache_ttl:
            _log.debug("bilibili | search cache HIT keyword={!r}", keyword)
            return entry.info

        from bilibili_api import search as bili_search

        try:
            result = await bili_search.search_by_type(
                keyword,
                search_type=bili_search.SearchObjectType.VIDEO,
                page=1,
            )
        except Exception as e:
            _log.warning("bilibili | search API failed keyword={!r} error={!r}", keyword, str(e)[:200])
            return None

        items = result.get("result") or []
        if not items:
            _log.warning("bilibili | search no results keyword={!r}", keyword)
            return None

        best = items[0]
        info = {
            "bvid": best.get("bvid", ""),
            "title": _strip_html(best.get("title", "")),
            "pic": best.get("pic", ""),
            "duration": _parse_duration(best.get("duration", "0:00")),
            "desc": _strip_html(best.get("description", "")),
            "stat": {
                "view": best.get("play", 0),
                "danmaku": best.get("video_review", 0),
            },
            "tname": _strip_html(best.get("typename", "")),
            "owner": {"name": _strip_html(best.get("author", ""))},
        }
        self._cache[cache_key] = _VideoCacheEntry(info=info, stored_at=now)
        return info

    async def _describe_cover(self, pic_url: str) -> str | None:
        """Download cover image and describe via vision client."""
        if not self._vision_client:
            return None
        async with httpx.AsyncClient(timeout=self._cover_timeout) as client:
            resp = await client.get(pic_url)
            if resp.status_code != 200:
                _log.debug("bilibili | cover download failed status={}", resp.status_code)
                return None
            image_data = resp.content

        media_type = resp.headers.get("content-type", "image/jpeg")
        return await self._vision_client.describe_image(
            image_data,
            media_type=media_type,
            prompt=_VIDEO_INFO_PROMPT,
        )

    async def _evaluate_interest_llm(self, title: str) -> float | None:
        """Evaluate interest via LLM. Returns 0.0-1.0 score or None on failure.

        Cached by title with the same TTL as video info.
        """
        cache_key = f"__interest__{title}"
        now = time.monotonic()
        entry = self._cache.get(cache_key)
        if entry and (now - entry.stored_at) < self._cache_ttl:
            _log.debug("bilibili | interest LLM cache HIT title={!r}", title)
            return entry.info

        system = [{"type": "text", "text": _INTEREST_LLM_PROMPT}]
        messages = [{"role": "user", "content": f"视频标题：{title}\n兴趣分（0-100）："}]

        try:
            result = await self._llm_client._call(system, messages, tools=None, max_tokens=512)
            raw = (result.get("text") or "").strip()
            if not raw:
                # deepseek-v4-flash may consume all tokens in thinking blocks
                thinking_blocks = result.get("thinking_blocks") or []
                for tb in thinking_blocks:
                    raw += tb.get("thinking", "")
                raw = raw.strip()
                _log.debug("bilibili | interest LLM text empty, falling back to thinking blocks len={}", len(raw))
            # Extract the first number (prompt asks to output number first)
            _log.info("bilibili | interest LLM raw={!r} title={!r}", raw[:300], title[:60])
            score = int(re.search(r"\d+", raw).group()) if re.search(r"\d+", raw) else None
        except Exception:
            _log.warning("bilibili | interest LLM call failed title={!r}", title)
            return None

        if score is None:
            return None
        clamped = max(0.0, min(1.0, score / 100.0))
        self._cache[cache_key] = _VideoCacheEntry(info=clamped, stored_at=now)
        return clamped

    def _inject_summary(self, ctx: MessageContext, summary: str) -> None:
        """Append video summary to message segments so _render_message picks it up."""
        segments = ctx.raw_message.get("segments")
        if segments is None:
            return

        # Append to the last text segment, or add a synthetic text segment
        for seg in reversed(segments):
            if getattr(seg, "type", "") == "text":
                data = getattr(seg, "data", {})
                if isinstance(data, dict):
                    existing = data.get("text", "")
                    data["text"] = f"{existing}\n\n{summary}" if existing else summary
                return

        # No text segment found — try to create one (this depends on OneBot Message internals)
        try:
            from nonebot.adapters.onebot.v11 import MessageSegment

            seg = MessageSegment.text(summary)
            if hasattr(segments, "append"):
                segments.append(seg)
        except Exception:
            pass
