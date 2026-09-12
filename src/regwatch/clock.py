"""时间工具。

统一时间戳口径，并让测试可以替换时钟（避免依赖真实 ``datetime.now``）。
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["now_iso", "today_iso", "utc_now"]


def utc_now() -> datetime:
    """返回带时区信息的当前时间。"""
    return datetime.now(UTC)


def now_iso() -> str:
    """秒级 ISO 时间戳（本地时间），用于任务与摘要记录。"""
    return datetime.now().isoformat(timespec="seconds")


def today_iso() -> str:
    """当天日期 ``YYYY-MM-DD``。"""
    return datetime.now().date().isoformat()
