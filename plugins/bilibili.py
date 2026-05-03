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
_INTEREST_LLM_FALLBACK = 0.6

_INTEREST_LLM_PROMPT = (
    "你是鳳笑梦（Emu Otori），一个乐园偶像。"
    "你对这个视频有多感兴趣？先只输出一个0到100的整数（不要任何其他文字），然后换行再解释。"
)

# Keywords that suggest a search result is a practice/cover/dance video rather
# than the original. Penalised during title match scoring.
_PRACTICE_SIGNALS: list[str] = [
    "自用", "镜面", "鏡面", "扒舞", "教程", "练习", "練習",
    "翻跳", "喊拍", "文字教程", "侵权删",
]

# Keywords that suggest a search result is the original/official video.
_ORIGINAL_SIGNALS: list[str] = [
    "世界计划", "pjsk", "mmj", "wxs", "vbs", "ln", "25時",
    "プロセカ", "colorful", "live", "公式", "mv", "original",
    "original song", "中日字幕", "官方",
]


def _title_match_score(keyword: str, title_html: str) -> float:
    """Score how well a search result title matches the intended video.

    Returns 0.0-1.0. Higher = more likely to be the correct video.
    """
    title = _strip_html(title_html).lower()
    kw = keyword.strip().lower()

    # Exact match
    if kw == title:
        return 1.0

    score: float

    if kw in title:
        # Keyword is a substring. Score based on how dominant it is in the title.
        ratio = len(kw) / max(len(title), 1)
        score = 0.6 + 0.3 * ratio
    else:
        # Character-level overlap for fuzzy matching
        kw_chars = set(kw)
        title_chars = set(title)
        if not kw_chars:
            return 0.0
        score = 0.3 * (len(kw_chars & title_chars) / len(kw_chars))

        # Prefix-word mismatch penalty: the first meaningful word of the
        # keyword (the key identifier — person name, character, work title)
        # MUST appear in the title. Otherwise this is almost certainly a
        # different video (e.g. "萍儿的低皮质醇" mismatched to "豹的低皮质醇~").
        prefix_word = _extract_first_word(kw)
        if prefix_word and prefix_word not in title:
            score *= 0.3

    # Penalize practice/mirror/dance-cover signals
    for sig in _PRACTICE_SIGNALS:
        if sig in title:
            score -= 0.15

    # Reward original/official content signals
    for sig in _ORIGINAL_SIGNALS:
        if sig.lower() in title:
            score += 0.05

    return max(0.0, min(1.0, score))


def _extract_first_word(text: str) -> str:
    """Extract the first meaningful word from text for identity matching.

    Strips leading bracket wrappers and punctuation, then returns the first
    2-4 characters as the likely identifier (name, work title, etc.).
    """
    import re

    # Strip leading 【...】 [...] （...） bracket wrappers
    text = re.sub(r"^【[^】]*】", "", text)
    text = re.sub(r"^\[[^\]]*\]", "", text)
    text = re.sub(r"^（[^）]*）", "", text)
    # Strip remaining punctuation / whitespace / symbols
    text = re.sub(r"[^\w一-鿿]", "", text)
    if len(text) >= 2:
        return text[:3].lower()
    return text.lower()


class BilibiliConfig(BaseModel):
    enabled: bool = True
    cache_ttl: float = 3600.0
    cover_timeout: float = 10.0
    reply_mode: str = "mood"  # "mood" | "always" | "dedicated" | "autonomous"
    bilibili_talk_value: float = 0.8  # base probability for dedicated/autonomous modes
    interest_llm_fallback: float = 0.6  # trigger LLM eval when keyword score < this
    high_interest_keywords: list[str] = []
    medium_interest_keywords: list[str] = []
    low_interest_keywords: list[str] = []


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
    # Project Sekai groups & characters
    "25时", "nightcord", "ニーゴ", "25ji",
    "vivid", "bad squad", "vbs", "ビビバス",
    "more more jump", "mmj", "モモジャン",
    "leo/need", "レオニ",
    "宵崎", "朝比奈", "東雲", "东云", "暁山", "晓山",
    "花里", "白石", "小豆沢", "青柳", "桃井",
    "日野森", "星乃", "天馬咲希", "望月",
    "rin", "len", "luka", "リン", "レン", "ルカ",
    # Related content
    "缤纷舞台", "プロジェクトセカイ",
    "mmd", "3d", "blender",
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
    # Fan works & adjacent content
    "手书", "手描き", "描いてみた",
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


def evaluate_interest(
    title: str,
    high_keywords: list[str] | None = None,
    med_keywords: list[str] | None = None,
    low_keywords: list[str] | None = None,
) -> float:
    """Compute a 0.0-1.0 interest score for a video title against the bot's persona.

    Keywords default to the module-level constants for backward compatibility
    (tests, callers that don't have a BilibiliPlugin instance).
    """
    title_lower = title.lower()
    score = 0.05  # floor — even unrecognized videos have a tiny baseline
    for kw in (high_keywords if high_keywords is not None else _HIGH_INTEREST):
        if kw in title_lower:
            score += 0.25
    for kw in (med_keywords if med_keywords is not None else _MEDIUM_INTEREST):
        if kw in title_lower:
            score += 0.12
    for kw in (low_keywords if low_keywords is not None else _LOW_INTEREST):
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
    version = "1.1.2"
    priority = 190

    async def on_startup(self, ctx: PluginContext) -> None:
        from kernel.config import load_plugin_config

        cfg = load_plugin_config("plugins/bilibili.toml", BilibiliConfig)
        self._enabled = cfg.enabled
        self._cache_ttl = cfg.cache_ttl
        self._cover_timeout = cfg.cover_timeout
        self._reply_mode = cfg.reply_mode
        self._bilibili_talk_value = cfg.bilibili_talk_value
        self._interest_llm_fallback = cfg.interest_llm_fallback
        self._high_keywords = cfg.high_interest_keywords
        self._med_keywords = cfg.medium_interest_keywords
        self._low_keywords = cfg.low_interest_keywords
        self._vision_client = getattr(ctx, "vision_client", None)
        self._llm_client = getattr(ctx, "llm_client", None)
        self._cache: dict[str, _VideoCacheEntry] = {}

        if self._enabled:
            _log.info(
                "bilibili plugin enabled | cache_ttl={}s cover_timeout={}s reply_mode={} "
                "keywords high={} med={} low={} llm_fallback={:.2f}",
                self._cache_ttl, self._cover_timeout, self._reply_mode,
                len(self._high_keywords), len(self._med_keywords), len(self._low_keywords),
                self._interest_llm_fallback,
            )
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
                # Try all URLs found in the card — QQ embeds multiple URL
                # formats (qqdocurl, share_url, url) in different locations.
                for card_url in json_info.get("_all_urls", []):
                    resolved = await self._resolve_urls_to_vid(card_url)
                    if resolved:
                        vid = resolved
                        _log.info(
                            "bilibili | json card url resolved | url={!r} -> vid={}",
                            card_url[:80], vid.key,
                        )
                        break

                if vid is None:
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
                interest = evaluate_interest(
                    title,
                    high_keywords=getattr(self, "_high_keywords", None),
                    med_keywords=getattr(self, "_med_keywords", None),
                    low_keywords=getattr(self, "_low_keywords", None),
                )
                if interest < getattr(self, "_interest_llm_fallback", _INTEREST_LLM_FALLBACK) and self._llm_client:
                    llm_score = await self._evaluate_interest_llm(title)
                    if llm_score is not None:
                        _log.info(
                            "bilibili | vid={} keyword={:.2f} llm={:.2f} title={!r}",
                            vid_key, interest, llm_score, title[:60],
                        )
                        interest = max(interest, llm_score)
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
                    _log.debug("bilibili | json seg data={}", raw[:2000])
                    parts.append(raw)
            elif seg_type == "forward" and isinstance(seg_data, dict):
                raw = seg_data.get("id", "")
                if raw:
                    parts.append(raw)

        return "".join(parts)

    @staticmethod
    def _extract_bilibili_json_info(ctx: MessageContext) -> dict | None:
        """Extract B站 video metadata from a QQ mini-program JSON card.

        QQ mini-program cards for B站 contain metadata (title, cover preview).
        Some cards also include a URL that can be resolved to a direct BV ID.
        Returns a dict with title/search query info, or None if this isn't a B站 card.
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
            if not isinstance(title, str) or not title:
                continue

            result: dict = {"title": title, "preview": detail.get("preview", "")}

            # Extract URL from multiple possible locations:
            # - detail_1.url / detail_1.qqdocurl (QQ doc redirect)
            # - detail_1.share_url (some card formats)
            # - meta.url / meta.qqdocurl (top-level meta)
            # - data.url / data.qqdocurl (top-level data, rare)
            candidate_urls: list[str] = []
            for source in (detail, meta, data):
                if not isinstance(source, dict):
                    continue
                for key in ("url", "qqdocurl", "share_url"):
                    val = source.get(key, "")
                    if isinstance(val, str) and val.strip():
                        candidate_urls.append(val.strip())

            if candidate_urls:
                result["url"] = candidate_urls[0]
                result["_all_urls"] = candidate_urls
                _log.info(
                    "bilibili | json card urls={} | title={!r} first_url={!r}",
                    len(candidate_urls), title, candidate_urls[0][:120],
                )
                for i, u in enumerate(candidate_urls[1:], start=2):
                    _log.info("bilibili | json card url[{}]={!r}", i, u[:120])
            else:
                _log.info(
                    "bilibili | json card NO url | title={!r} detail_keys={}",
                    title, list(detail.keys()) if isinstance(detail, dict) else "n/a",
                )
                # Dump all field names across detail_1, meta, data for diagnosis
                for label, src in [("detail_1", detail), ("meta", meta), ("data", data)]:
                    if isinstance(src, dict):
                        _log.info("bilibili | json card {} keys={}", label, list(src.keys())[:20])

            return result

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

    @staticmethod
    async def _resolve_urls_to_vid(url: str) -> _VideoId | None:
        """Try to extract a video ID from a URL string.

        Handles direct bilibili URLs, b23.tv short links, and QQ document
        redirect URLs (qqdocurl) by following HTTP redirects.
        """
        # Already a full bilibili URL with BV/av
        vid = extract_video_id(url)
        if vid:
            return vid

        # Try to resolve b23.tv short links
        if _B23_PATTERN.search(url):
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                for m in _B23_PATTERN.finditer(url):
                    try:
                        resp = await client.get(f"https://b23.tv/{m.group(1)}")
                        vid = extract_video_id(str(resp.url))
                        if vid:
                            return vid
                    except Exception:
                        pass

        # Generic redirect follow for other URL shorteners / QQ doc redirects.
        # QQ mini-program cards often give scheme-less URLs like
        # "m.q.qq.com/a/s/..." — normalise to https first.
        normalized = url if "://" in url else f"https://{url}"
        if normalized.startswith("http") and not extract_video_id(normalized):
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                    resp = await client.get(normalized)
                    final = str(resp.url)
                    vid = extract_video_id(final)
                    if vid:
                        _log.info("bilibili | url redirect resolved | {} -> {}", url[:80], final[:80])
                        return vid
            except Exception:
                _log.debug("bilibili | url redirect failed | url={!r}", url[:80])

        return None

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
        """Search B站 for a video by title keyword.

        Fetches top results and picks the best match by title similarity,
        not blindly the first result.
        """
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

        # Score results by title similarity and pick the best match
        scored = [(item, _title_match_score(keyword, item.get("title", ""))) for item in items[:10]]
        scored.sort(key=lambda x: x[1], reverse=True)

        best, best_score = scored[0]
        _log.info(
            "bilibili | search keyword={!r} results={} best_score={:.2f} best_title={!r}",
            keyword, len(items), best_score, _strip_html(best.get("title", ""))[:60],
        )

        # Reject when confidence is too low — a bad match is worse than no match.
        if best_score < 0.3:
            _log.warning(
                "bilibili | search rejected low confidence keyword={!r} best_score={:.2f}",
                keyword, best_score,
            )
            return None
            runner_up, ru_score = scored[1]
            _log.debug(
                "bilibili | search runner_up score={:.2f} title={!r}",
                ru_score, _strip_html(runner_up.get("title", ""))[:60],
            )

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
