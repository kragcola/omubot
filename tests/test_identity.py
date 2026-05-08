import pytest

from services.identity import IdentityManager, parse_identity


def test_parse_identity_basic() -> None:
    md = """\
# 测试人设

你是一个测试机器人。
- 说话简洁
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "测试人设"
    assert "测试机器人" in identity.personality
    assert identity.proactive is None


def test_parse_identity_with_proactive() -> None:
    md = """\
# Bot

Bot 人设描述。

## 插话方式

有人求助时插话。
用 JSON 回答。
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "Bot"
    assert "Bot 人设描述" in identity.personality
    assert "插话方式" not in identity.personality
    assert identity.proactive == "有人求助时插话。\n用 JSON 回答。"


def test_parse_identity_no_title() -> None:
    md = "没有标题的内容"
    assert parse_identity(md) is None


def test_resolve_default() -> None:
    mgr = IdentityManager()
    identity = mgr.resolve()
    assert identity.id == "default"
    assert identity.name == "默认"


@pytest.mark.asyncio
async def test_load_file(tmp_path: object) -> None:
    import pathlib

    p = pathlib.Path(str(tmp_path)) / "identity.md"
    p.write_text("# 红莉栖\n\n天才物理学家。\n")

    mgr = IdentityManager()
    await mgr.load_file(p)
    identity = mgr.resolve()
    assert identity.name == "红莉栖"
    assert "天才物理学家" in identity.personality


# ---- Optional frontmatter compatibility tests ----


def test_parse_identity_frontmatter_basic() -> None:
    """Markdown with frontmatter name + H1 title + proactive section."""
    md = """\
---
name: 凤笑梦
description: Wonderlands×Showtime 的元气少女
---

# 凤笑梦 (Emu Otori)

你是一个元气满满的舞台少女。
喜欢用颜文字和表情包。

## 插话方式

有人提到舞台或表演时一定要插话。
被@时必须回复。
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "凤笑梦"  # frontmatter takes priority
    assert identity.description == "Wonderlands×Showtime 的元气少女"
    assert "元气满满的舞台少女" in identity.personality
    assert "插话方式" not in identity.personality
    assert "有人提到舞台或表演时一定要插话" in identity.proactive


def test_parse_identity_name_from_frontmatter_only() -> None:
    """Frontmatter name is used even without H1 in body."""
    md = """\
---
name: 测试Bot
description: 一个测试
---

这是人格描述，没有 H1 标题。
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "测试Bot"
    assert identity.description == "一个测试"
    assert "这是人格描述" in identity.personality
    assert identity.proactive is None


def test_parse_identity_frontmatter_name_fallback_to_h1() -> None:
    """H1 title is used when frontmatter has no name."""
    md = """\
---
description: 只有描述没有名字
---

# 红莉栖

天才少女。
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "红莉栖"
    assert identity.description == "只有描述没有名字"


def test_parse_identity_frontmatter_no_name_at_all() -> None:
    """Returns None when neither frontmatter name nor H1 exists."""
    md = """\
---
description: 无名称
---

没有标题的内容。
"""
    assert parse_identity(md) is None


def test_parse_identity_invalid_frontmatter_yaml() -> None:
    """Gracefully handles invalid YAML frontmatter — falls back to body parsing."""
    md = """\
---
name: [unclosed
---

# Bot

人格。
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "Bot"
    assert "人格" in identity.personality


def test_parse_identity_no_frontmatter_still_works() -> None:
    """Plain Markdown without frontmatter still works (backward compatibility)."""
    md = """\
# 旧格式

没有 frontmatter 的人设。

## 插话方式

旧格式的插话规则。
"""
    identity = parse_identity(md)
    assert identity is not None
    assert identity.name == "旧格式"
    assert identity.description == ""
    assert "没有 frontmatter 的人设" in identity.personality
    assert identity.proactive == "旧格式的插话规则。"
