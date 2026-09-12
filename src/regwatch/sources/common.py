"""采集层通用工具。

历史上 ``amac.py`` / ``csrc.py`` / ``amac_monthly.py`` 各有一份且实现已漂移：
``sanitize_filename`` 三份、``extract_id_from_url`` 两份（兜底规则不同）、
``parse_date_from_text`` 两份（只有一份带 ``ValueError`` 保护）。
这里收敛为单一实现。
"""

from __future__ import annotations

import re
from datetime import date, datetime

__all__ = [
    "extract_amac_case_id",
    "extract_csrc_case_id",
    "parse_date_from_text",
    "sanitize_filename",
]

#: 文件名中的非法字符
_ILLEGAL_CHARS = re.compile(r'[\\/*?:"<>|]')


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符。"""
    return _ILLEGAL_CHARS.sub("", name).strip()


def parse_date_from_text(text: str) -> date | None:
    """从文本中解析日期，支持 ``YYYY-MM-DD`` 与 ``YYYY年MM月DD日``。

    无法解析（含 2026-02-31 这类非法日期）时返回 ``None``。
    """
    if not text:
        return None
    match = re.search(r"(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})", text)
    if match:
        parsed = _safe_date(*(int(group) for group in match.groups()))
        if parsed is not None:
            return parsed
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if match:
        parsed = _safe_date(*(int(group) for group in match.groups()))
        if parsed is not None:
            return parsed
    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def extract_amac_case_id(url: str) -> str:
    """从 AMAC 详情页 URL 提取案例标识。

    依次尝试 ``P{20位}`` 编号、``/t{日期_序号}`` 段、``/{6位日期}/{slug}``，
    最后退化为末段清洗。
    """
    match = re.search(r"(P\d{20,})", url)
    if match:
        return match.group(1)
    match = re.search(r"/t(\d+_\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/(\d{6}/[^/]+)$", url)
    if match:
        return re.sub(r"[^a-zA-Z0-9]", "_", match.group(1))
    return _slug(url)


def extract_csrc_case_id(url: str) -> str:
    """从 CSRC 详情页 URL 提取内容编号，如 ``c7615688``。"""
    match = re.search(r"/(c\d+)/content\.shtml", url)
    if match:
        return match.group(1)
    match = re.search(r"(c\d{6,})", url)
    if match:
        return match.group(1)
    return _slug(url)


def _slug(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", url.split("/")[-1])[:60]
