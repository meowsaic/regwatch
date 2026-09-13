"""时间工具。

统一时间戳口径，便于集中调整格式。
"""

from __future__ import annotations

from datetime import datetime

__all__ = ["now_iso"]


def now_iso() -> str:
    """秒级 ISO 时间戳（本地时间），用于任务与摘要记录。"""
    return datetime.now().isoformat(timespec="seconds")
