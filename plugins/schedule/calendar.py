"""Calendar — real-date awareness for schedule generation.

Provides day type (school/weekend/holiday/makeup), public holidays,
character birthdays, and special non-holiday festivals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

_self_name: str | None = None


def set_self_name(name: str) -> None:
    """Set the bot's own name for birthday detection."""
    global _self_name
    _self_name = name


def get_self_name() -> str | None:
    """Get the bot's own name."""
    return _self_name


@dataclass
class BirthdayEntry:
    """A Project Sekai character's birthday."""

    name_cn: str
    name_jp: str
    group: str  # "Wonderlands×Showtime", "Leo/need", etc.
    is_wxs_member: bool = False


@dataclass
class DayContext:
    """Everything special about a given date."""

    date: str  # "YYYY-MM-DD"
    weekday: int  # 0=Mon … 6=Sun
    day_type: str  # "school_day" | "weekend" | "holiday" | "makeup_day"
    holiday_name: str = ""  # e.g. "春节", "国庆节"
    birthdays: list[BirthdayEntry] = field(default_factory=list)
    special_day: str = ""  # non-holiday festival, e.g. "七夕", "圣诞节"

    @property
    def is_school_day(self) -> bool:
        return self.day_type == "school_day"

    @property
    def is_weekend(self) -> bool:
        return self.day_type == "weekend"

    @property
    def is_holiday(self) -> bool:
        return self.day_type == "holiday"

    @property
    def is_makeup_day(self) -> bool:
        return self.day_type == "makeup_day"

    @property
    def has_birthday(self) -> bool:
        return len(self.birthdays) > 0

    @property
    def is_self_birthday(self) -> bool:
        if _self_name is None:
            return False
        return any(b.name_cn == _self_name for b in self.birthdays)

    def wxs_birthdays(self) -> list[BirthdayEntry]:
        return [b for b in self.birthdays if b.is_wxs_member]

    def other_birthdays(self) -> list[BirthdayEntry]:
        return [b for b in self.birthdays if not b.is_wxs_member]


# ============================================================================
# 2026 中国法定节假日（国办发明电〔2025〕7号）
# ============================================================================

_HOLIDAYS_2026: dict[str, str] = {
    # 元旦 1/1-1/3
    "2026-01-01": "元旦",
    "2026-01-02": "元旦",
    "2026-01-03": "元旦",
    # 春节 2/15-2/23
    "2026-02-15": "春节",
    "2026-02-16": "春节",
    "2026-02-17": "春节",
    "2026-02-18": "春节",
    "2026-02-19": "春节",
    "2026-02-20": "春节",
    "2026-02-21": "春节",
    "2026-02-22": "春节",
    "2026-02-23": "春节",
    # 清明 4/4-4/6
    "2026-04-04": "清明节",
    "2026-04-05": "清明节",
    "2026-04-06": "清明节",
    # 劳动节 5/1-5/5
    "2026-05-01": "劳动节",
    "2026-05-02": "劳动节",
    "2026-05-03": "劳动节",
    "2026-05-04": "劳动节",
    "2026-05-05": "劳动节",
    # 端午 6/19-6/21
    "2026-06-19": "端午节",
    "2026-06-20": "端午节",
    "2026-06-21": "端午节",
    # 中秋 9/25-9/27
    "2026-09-25": "中秋节",
    "2026-09-26": "中秋节",
    "2026-09-27": "中秋节",
    # 国庆 10/1-10/7
    "2026-10-01": "国庆节",
    "2026-10-02": "国庆节",
    "2026-10-03": "国庆节",
    "2026-10-04": "国庆节",
    "2026-10-05": "国庆节",
    "2026-10-06": "国庆节",
    "2026-10-07": "国庆节",
}

# 调休上班日
_MAKEUP_DAYS_2026: set[str] = {
    "2026-01-04",  # 元旦调休
    "2026-02-14",  # 春节调休
    "2026-02-28",  # 春节调休
    "2026-05-09",  # 劳动节调休
    "2026-09-20",  # 国庆调休
    "2026-10-10",  # 国庆调休
}

# 不放假的特殊节日（农历节日取公历近似值用于2026）
_SPECIAL_DAYS_2026: dict[str, str] = {
    "2026-02-02": "春季节分",  # 节分
    "2026-02-14": "情人节",
    "2026-03-03": "女儿节",  # 雏祭
    "2026-03-14": "白色情人节",
    "2026-04-01": "愚人节",
    "2026-05-10": "母亲节",
    "2026-06-01": "儿童节",
    "2026-06-21": "父亲节",
    "2026-07-07": "七夕",
    "2026-08-29": "初音未来周年纪念日",  # 8/31 但近似的周末
    "2026-10-31": "万圣节",
    "2026-11-01": "万圣节次",
    "2026-12-24": "平安夜",
    "2026-12-25": "圣诞节",
    "2026-12-31": "除夕夜",  # 跨年
}

# ============================================================================
# 世界计划 缤纷舞台！全 26 位角色生日
# ============================================================================

_BIRTHDAYS_MMDD: dict[str, list[BirthdayEntry]] = {
    "01-08": [
        BirthdayEntry(name_cn="日野森志步", name_jp="日野森志歩", group="Leo/need"),
    ],
    "01-27": [
        BirthdayEntry(name_cn="朝比奈真冬", name_jp="朝比奈まふゆ", group="25点，Nightcord见。"),
    ],
    "01-30": [
        BirthdayEntry(name_cn="巡音流歌", name_jp="巡音ルカ", group="VIRTUAL SINGER"),
    ],
    "02-10": [
        BirthdayEntry(name_cn="宵崎奏", name_jp="宵崎奏", group="25点，Nightcord见。"),
    ],
    "02-17": [
        BirthdayEntry(name_cn="KAITO", name_jp="KAITO", group="VIRTUAL SINGER"),
    ],
    "03-02": [
        BirthdayEntry(name_cn="小豆泽心羽", name_jp="小豆沢こはね", group="Vivid BAD SQUAD"),
    ],
    "03-19": [
        BirthdayEntry(name_cn="桃井爱莉", name_jp="桃井愛莉", group="MORE MORE JUMP!"),
    ],
    "04-14": [
        BirthdayEntry(name_cn="花里实乃理", name_jp="花里みのり", group="MORE MORE JUMP!"),
    ],
    "04-30": [
        BirthdayEntry(name_cn="东云绘名", name_jp="東雲絵名", group="25点，Nightcord见。"),
    ],
    "05-09": [
        BirthdayEntry(name_cn="天马咲希", name_jp="天馬咲希", group="Leo/need"),
    ],
    "05-17": [
        BirthdayEntry(name_cn="天马司", name_jp="天馬司", group="Wonderlands×Showtime", is_wxs_member=True),
    ],
    "05-25": [
        BirthdayEntry(name_cn="青柳冬弥", name_jp="青柳冬弥", group="Vivid BAD SQUAD"),
    ],
    "06-24": [
        BirthdayEntry(name_cn="神代类", name_jp="神代類", group="Wonderlands×Showtime", is_wxs_member=True),
    ],
    "07-20": [
        BirthdayEntry(name_cn="草薙宁宁", name_jp="草薙寧々", group="Wonderlands×Showtime", is_wxs_member=True),
    ],
    "07-26": [
        BirthdayEntry(name_cn="白石杏", name_jp="白石杏", group="Vivid BAD SQUAD"),
    ],
    "08-11": [
        BirthdayEntry(name_cn="星乃一歌", name_jp="星乃一歌", group="Leo/need"),
    ],
    "08-27": [
        BirthdayEntry(name_cn="晓山瑞希", name_jp="暁山瑞希", group="25点，Nightcord见。"),
    ],
    "08-31": [
        BirthdayEntry(name_cn="初音未来", name_jp="初音ミク", group="VIRTUAL SINGER"),
    ],
    "09-09": [
        BirthdayEntry(name_cn="凤笑梦", name_jp="鳳笑夢", group="Wonderlands×Showtime", is_wxs_member=True),
    ],
    "10-05": [
        BirthdayEntry(name_cn="桐谷遥", name_jp="桐谷遥", group="MORE MORE JUMP!"),
    ],
    "10-27": [
        BirthdayEntry(name_cn="望月穗波", name_jp="望月穂波", group="Leo/need"),
    ],
    "11-05": [
        BirthdayEntry(name_cn="MEIKO", name_jp="MEIKO", group="VIRTUAL SINGER"),
    ],
    "11-12": [
        BirthdayEntry(name_cn="东云彰人", name_jp="東雲彰人", group="Vivid BAD SQUAD"),
    ],
    "12-06": [
        BirthdayEntry(name_cn="日野森雫", name_jp="日野森雫", group="MORE MORE JUMP!"),
    ],
    "12-27": [
        BirthdayEntry(name_cn="镜音铃", name_jp="鏡音リン", group="VIRTUAL SINGER"),
        BirthdayEntry(name_cn="镜音连", name_jp="鏡音レン", group="VIRTUAL SINGER"),
    ],
}


# ============================================================================
# Public API
# ============================================================================


def get_day_context(dt: datetime) -> DayContext:
    """Build the DayContext for a given datetime (assumed CST)."""
    date_str = dt.strftime("%Y-%m-%d")
    mmdd = dt.strftime("%m-%d")
    wd = dt.weekday()  # 0=Mon

    holiday_name = _HOLIDAYS_2026.get(date_str, "")
    is_makeup = date_str in _MAKEUP_DAYS_2026
    special_day = _SPECIAL_DAYS_2026.get(date_str, "")
    birthdays = _BIRTHDAYS_MMDD.get(mmdd, [])

    # Determine day type
    if holiday_name:
        day_type = "holiday"
    elif is_makeup:
        day_type = "makeup_day"
    elif wd < 5:
        day_type = "school_day"
    else:
        day_type = "weekend"

    return DayContext(
        date=date_str,
        weekday=wd,
        day_type=day_type,
        holiday_name=holiday_name,
        birthdays=birthdays,
        special_day=special_day,
    )
