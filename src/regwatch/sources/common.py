"""采集层通用工具。

历史上 ``amac.py`` / ``csrc.py`` 各有一份且实现已漂移：
``sanitize_filename`` 三份、``extract_id_from_url`` 两份（兜底规则不同）、
``parse_date_from_text`` 两份（只有一份带 ``ValueError`` 保护）。
这里收敛为单一实现；采集进度上报也集中在此。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..domain import Dataset

__all__ = [
    "FETCH_OVERLAP_DAYS",
    "Progress",
    "ProgressHook",
    "default_fetch_range",
    "extract_amac_case_id",
    "extract_csrc_case_id",
    "parse_date_from_text",
    "parse_iso_date",
    "progress",
    "sanitize_filename",
    "split_csv",
]

#: 增量抓取的默认回看天数：从「上次已覆盖日期」再往前多扫几天，防止边界遗漏
FETCH_OVERLAP_DAYS = 7

logger = logging.getLogger("regwatch.sources")

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


def split_csv(value: str | None) -> list[str]:
    """按逗号拆分采集参数（``--bureaus HQ,Beijing``），去掉空白项。"""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_iso_date(value: str | None, field_name: str = "日期") -> date | None:
    """解析采集参数里的 ``YYYY-MM-DD`` 日期；空值返回 ``None``。

    Raises:
        ValueError: 格式非法；消息可直接展示给用户。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} 日期格式应为 YYYY-MM-DD，收到：{text!r}") from exc


def _latest_covered_date(store: Any, dataset: Dataset) -> date | None:
    """库中该数据集已有案例的最新日期；库为空或日期不可解析时返回 ``None``。"""
    if store is None:
        return None
    from ..domain import CaseQuery

    _, date_max = store.cases.date_range(CaseQuery(datasets=(dataset,)))
    return parse_date_from_text(date_max)


def default_fetch_range(
    store: Any,
    dataset: Dataset,
    *,
    first_run_start: date,
    today: date | None = None,
    overlap_days: int = FETCH_OVERLAP_DAYS,
) -> tuple[date, date]:
    """增量抓取的默认日期区间：``上次覆盖日期 - 回看天数`` ~ ``今天``。

    「上次覆盖日期」取库中该数据集案例的最大 ``date``；空库（首次抓取）
    回落到 ``first_run_start``。结束日期每次调用时按当天计算。
    """
    end = today or datetime.now().date()
    latest = _latest_covered_date(store, dataset)
    if latest is None:
        return first_run_start, end
    start = latest - timedelta(days=max(0, int(overlap_days)))
    return min(start, end), end


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


# ──────────────────────────── 进度上报 ────────────────────────────

ProgressHook = Callable[[int, int, str], None]


def progress(msg: str) -> None:
    """输出一行进度信息（INFO 级日志）。"""
    logger.info(msg)


@dataclass(slots=True)
class Progress:
    """一次抓取任务的进度上报器：既记日志也回调上层进度条。"""

    callback: ProgressHook | None = None
    total: int = 0
    done: int = 0

    def start(self, total: int, message: str = "") -> None:
        """设置总数并上报起点。"""
        self.total = max(0, int(total))
        self.done = 0
        self.update(0, message=message)

    def update(self, done: int, total: int | None = None, message: str = "") -> None:
        """更新进度并回调上层。"""
        if total is not None:
            self.total = max(self.total, int(total))
        self.done = int(done)
        if self.callback is not None:
            self.callback(self.done, self.total, message)

    def advance(self, step: int = 1, message: str = "") -> None:
        """完成一项并上报。"""
        self.update(self.done + step, message=message)
