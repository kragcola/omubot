"""Finite Bilibili reference/response shapes; no IO or authority in parsing."""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import cast
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import JsonValue

from .rich_messages import VideoRef
from .types import OperationError
from .video_refs import parse_bilibili_card, parse_video_url

_IDS = re.compile(r'(BV[0-9A-Za-z]{10}|av[0-9]{1,12})\Z')
_URLS = re.compile(r'https?://[^\s<>\"\x00-\x20]{1,2048}|(?<![\w.])b23\.tv/[A-Za-z0-9]{1,16}(?![\w/])')
_TAGS = re.compile(r'<[^>]{0,512}>')
_PRACTICE = ('自用', '镜面', '鏡面', '扒舞', '教程', '练习', '練習', '翻跳', '喊拍', '文字教程', '侵权删')
_ORIGINAL = ('世界计划', 'pjsk', 'mmj', 'wxs', 'vbs', 'ln', '25時', 'プロセカ', 'colorful',
             'live', '公式', 'mv', 'original', 'original song', '中日字幕', '官方')


def _title_score(keyword: str, title: str) -> float:
    kw, text = keyword.lower().strip(), title.lower()
    if kw == text:
        return 1.0
    if kw in text:
        score = 0.6 + 0.3 * len(kw) / max(len(text), 1)
    else:
        score = 0.3 * len(set(kw) & set(text)) / max(len(set(kw)), 1)
        prefix = kw
        for pattern in (r'^【[^】]*】', r'^\[[^\]]*\]', r'^（[^）]*）'):
            prefix = re.sub(pattern, '', prefix)
        prefix = re.sub(r'[^\w一-鿿]', '', prefix)[:3]
        if prefix and prefix not in text:
            score *= 0.3
    score -= 0.15 * sum(signal in text for signal in _PRACTICE)
    score += 0.05 * sum(signal in text for signal in _ORIGINAL)
    return max(0.0, min(1.0, score))


def extended_bilibili_url(value: str, *, title: str = '', card: bool = False) -> VideoRef | None:
    if value.startswith(('b23.tv/', 'm.q.qq.com/')):
        value = 'https://' + value
    direct = parse_video_url(value)
    if direct is not None and direct.platform == 'bilibili':
        return VideoRef('bilibili', direct.video_id, direct.url, title)
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {'http', 'https'} or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)):
            return None
    except ValueError:
        return None
    if parts.hostname in {'bilibili.com', 'www.bilibili.com', 'm.bilibili.com'}:
        match = re.fullmatch(r'/bangumi/play/(ep|ss)([0-9]{1,12})/?', parts.path)
        if match:
            identity = ''.join(match.groups())
            return VideoRef('bilibili', identity, 'https://www.bilibili.com/bangumi/play/' + identity,
                            title, 'episode' if match[1] == 'ep' else 'season')
    if parts.hostname == 'b23.tv' and re.fullmatch(r'/[A-Za-z0-9]{1,16}/?', parts.path):
        identity = parts.path.strip('/')
        return VideoRef('bilibili', 'b23:' + identity, 'https://b23.tv/' + identity, title, 'short')
    if (card and parts.hostname == 'm.q.qq.com'
            and re.fullmatch(r'/a/s/[A-Za-z0-9_-]{1,128}/?', parts.path)):
        path = parts.path.rstrip('/')
        return VideoRef('bilibili', 'qqdoc:' + path.rsplit('/', 1)[-1], 'https://m.q.qq.com' + path,
                        title, 'qqdoc')
    return None


def extended_bilibili_text(text: str) -> tuple[VideoRef, ...]:
    refs: dict[str, VideoRef] = {}
    for match in _URLS.finditer(text[:8000]):
        ref = extended_bilibili_url(match[0].rstrip('。，、！？.,!?)］】'))
        if ref is not None:
            refs.setdefault(ref.video_id, ref)
            if len(refs) == 2:
                break
    return tuple(refs.values())


def extended_bilibili_card(raw: str) -> tuple[VideoRef, ...]:
    direct = parse_bilibili_card(raw)
    if direct:
        return direct
    try:
        card = cast(JsonValue, json.loads(raw))
    except (ValueError, RecursionError):
        return ()
    meta = card.get('meta') if isinstance(card, dict) else None
    detail = meta.get('detail_1') if isinstance(meta, dict) else None
    if not isinstance(detail, dict) or str(detail.get('appid', '')) != '1109937557':
        return ()
    title_value = detail.get('desc') or detail.get('title')
    title = ' '.join(title_value.split())[:160] if isinstance(title_value, str) else ''
    for location in (detail, meta, card):
        assert isinstance(location, dict)
        for key in ('url', 'qqdocurl', 'share_url'):
            value = location.get(key)
            if isinstance(value, str) and len(value) <= 2048:
                ref = extended_bilibili_url(value.strip(), title=title, card=True)
                if ref is not None:
                    return (ref,)
    if title:
        return (VideoRef('bilibili', 'title:' + hashlib.sha256(title.encode()).hexdigest(),
                         'https://search.bilibili.com/video?' + urlencode({'keyword': title}),
                         title, 'card_title'),)
    return ()


def bili_endpoint(ref: VideoRef) -> str:
    root = 'https://api.bilibili.com'
    if ref.reference_kind in {'short', 'qqdoc'}:
        return ref.url
    if ref.reference_kind == 'video' and _IDS.fullmatch(ref.video_id):
        return root + '/x/web-interface/view?' + urlencode(
            {'bvid': ref.video_id} if ref.video_id.startswith('BV') else {'aid': ref.video_id[2:]})
    if ref.reference_kind in {'episode', 'season'}:
        return root + '/pgc/view/web/season?' + urlencode(
            {'ep_id' if ref.reference_kind == 'episode' else 'season_id': ref.video_id[2:]})
    if ref.reference_kind == 'card_title' and ref.title:
        return root + '/x/web-interface/search/type?' + urlencode(
            {'search_type': 'video', 'page': '1', 'keyword': ref.title})
    raise OperationError('video_metadata_protocol')


def validate_bili_step_url(url: str) -> None:
    """Only fixed metadata routes and the two authenticated short-card shapes."""
    p = urlsplit(url)
    ref = extended_bilibili_url(url, card=True)
    if ref is not None and ref.reference_kind in {'short', 'qqdoc'} and url == ref.url:
        return
    if p.hostname != 'api.bilibili.com':
        raise OperationError('video_step_destination_denied')
    q = parse_qs(p.query, keep_blank_values=True)
    if any(len(values) != 1 for values in q.values()):
        raise OperationError('video_step_destination_denied')
    if p.path == '/x/web-interface/view':
        allowed = ((set(q) == {'bvid'} and bool(_IDS.fullmatch(q['bvid'][0]))
                    and q['bvid'][0].startswith('BV')) or
                   (set(q) == {'aid'} and bool(re.fullmatch(r'[0-9]{1,12}', q['aid'][0]))))
    elif p.path == '/pgc/view/web/season':
        allowed = (set(q) in ({'ep_id'}, {'season_id'})
                   and bool(re.fullmatch(r'[0-9]{1,12}', next(iter(q.values()))[0])))
    elif p.path == '/x/web-interface/search/type':
        allowed = (set(q) == {'search_type', 'page', 'keyword'} and q['search_type'] == ['video']
                   and q['page'] == ['1'] and 1 <= len(q['keyword'][0]) <= 160)
    else:
        allowed = False
    if not allowed:
        raise OperationError('video_step_destination_denied')


def _video(data: dict[str, JsonValue]) -> tuple[str, str | None, bool]:
    identity = data.get('bvid')
    if not isinstance(identity, str) or not _IDS.fullmatch(identity):
        aid = data.get('aid')
        identity = 'av' + str(aid) if type(aid) is int and 0 < aid < 10**12 else ''
    if not _IDS.fullmatch(identity):
        raise OperationError('video_metadata_protocol')
    value = data.get('title')
    if value is None:
        return identity, None, False
    if not isinstance(value, str):
        raise OperationError('video_metadata_protocol')
    title = ' '.join(html.unescape(_TAGS.sub('', value)).split())
    return identity, title[:160] or None, len(title) > 160


def resolve_bangumi_video(ref: VideoRef, text: str) -> VideoRef | None:
    payload = _payload(text)
    if ref.reference_kind == 'season' and payload.get('season_id') != int(ref.video_id[2:]):
        raise OperationError('video_metadata_reference_mismatch')
    episodes = payload.get('episodes')
    if not isinstance(episodes, list):
        raise OperationError('video_metadata_protocol')
    candidates = [item for item in episodes[:100] if isinstance(item, dict)]
    if ref.reference_kind == 'episode':
        candidates = [item for item in candidates if item.get('id') == int(ref.video_id[2:])]
    else:
        playable = [item for item in candidates if isinstance(item.get('rights'), dict)
                    and cast(dict[str, JsonValue], item['rights']).get('can_watch') == 1]
        candidates = playable or candidates
    if not candidates:
        return None
    identity, _, _ = _video(candidates[0])
    return VideoRef('bilibili', identity, 'https://www.bilibili.com/video/' + identity, ref.title)


def _payload(text: str) -> dict[str, JsonValue]:
    try:
        payload = cast(JsonValue, json.loads(text))
    except (ValueError, RecursionError) as exc:
        raise OperationError('video_metadata_protocol') from exc
    if not isinstance(payload, dict) or type(payload.get('code')) is not int:
        raise OperationError('video_metadata_protocol')
    if payload['code'] != 0:
        raise OperationError('video_metadata_unavailable')
    data = payload.get('data', payload.get('result'))
    if not isinstance(data, dict):
        raise OperationError('video_metadata_protocol')
    return data


def decode_bili_metadata(ref: VideoRef, text: str) -> tuple[str | None, bool]:
    data = _payload(text)
    if ref.reference_kind == 'video':
        identity, title, partial = _video(data)
        if ref.video_id.startswith('BV'):
            matches = identity == ref.video_id
        else:
            matches = data.get('aid') == int(ref.video_id[2:])
        if not matches:
            raise OperationError('video_metadata_reference_mismatch')
        return title, partial
    if ref.reference_kind == 'card_title':
        values = data.get('result')
        if not isinstance(values, list):
            raise OperationError('video_metadata_protocol')
        scored: list[tuple[float, str | None, bool]] = []
        kw = ref.title.lower()
        for item in values[:10]:
            if not isinstance(item, dict):
                continue
            _, title, partial = _video(item)
            if title is None:
                continue
            score = _title_score(kw, title)
            scored.append((score, title, partial))
        if not scored:
            return None, False
        score, title, partial = max(scored, key=lambda item: item[0])
        return (title, partial) if score >= 0.3 else (None, False)
    raise OperationError('video_metadata_protocol')
