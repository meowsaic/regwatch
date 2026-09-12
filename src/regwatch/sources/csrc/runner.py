"""CSRC 抓取编排：多来源 × 多类型的并发抓取与对外 API。

抓取状态无需额外的索引文件：案例以 ``(dataset, case_id)`` 为主键落库，
「是否已抓过」直接查库即可，因此断点续传天然成立。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ...domain import CaseRecord, CaseStatus, CaseType, Dataset
from ...logging_setup import get_logger
from ..bureaus import BUREAUS, Bureau, get_bureau_by_name
from ..http import close_shared_session
from ..progress import progress
from .constants import (
    CASE_TYPE_CN,
    CASE_TYPE_MEASURE,
    CASE_TYPE_PENALTY,
    DEFAULT_CONCURRENCY,
    DEFAULT_START_DATE,
    DELAY_BETWEEN_CASES,
    MAX_CONCURRENCY,
    MIN_CONCURRENCY,
)
from .discovery import collect_measure_links, collect_penalty_links
from .fetcher import process_case
from .models import CaseData, is_fund_by_title

logger = get_logger("sources.csrc")

__all__ = [
    "FetchResult",
    "available_bureaus",
    "default_start_date",
    "fetch",
    "fetch_pending",
    "fetch_single",
    "fetch_source",
]

_MIN_TEXT_LEN = 50


# ──────────────────────────── 单来源抓取 ────────────────────────────


def fetch_source(
    bureau: Bureau,
    case_type: str,
    start_date: date,
    end_date: date,
    *,
    store: Any,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, int, int]:
    """抓取单个来源（局 × 类型）的全部案例。

    Returns:
        ``(success, failed, skipped)`` 三类计数。
    """
    workers = max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, int(concurrency)))
    logger.info("=" * 20 + f" {bureau.name_cn} / {CASE_TYPE_CN[case_type]} " + "=" * 20)

    # 1) 刷新列表页（成本低，每次都刷以保证完整性）
    logger.info("  从官网爬取案例列表...")
    collect = collect_measure_links if case_type == CASE_TYPE_MEASURE else collect_penalty_links
    links = collect(bureau, start_date, end_date)
    logger.info("  列表页收集到 %d 个案例", len(links))

    # 2) 过滤：库里已有正文的跳过；标题明确非基金的直接标记 skipped
    known = set(store.cases.ids(Dataset.CSRC))
    to_process: list[dict[str, Any]] = []
    skipped = 0

    for item in links:
        if _case_id_of(item, bureau, case_type) in known:
            continue
        if is_fund_by_title(item.get("title", "")) is False:
            store.cases.upsert(
                _minimal_record(item, bureau, case_type, CaseStatus.SKIPPED, "标题明确非基金")
            )
            skipped += 1
            continue
        to_process.append(item)

    if not to_process:
        logger.info("  %s/%s: 无待处理案例", bureau.name_cn, CASE_TYPE_CN[case_type])
        return 0, 0, skipped

    logger.info("  待处理 %d 个（已跳过非基金 %d 个）| 并发 %d", len(to_process), skipped, workers)

    counts = {"success": 0, "failed": 0, "skipped": 0}
    total = len(to_process)

    def worker(link_info: dict[str, Any]) -> tuple[str, CaseData | None, str]:
        link_url = link_info["link_url"]
        title = link_info.get("title", "")
        date_str = link_info.get("date", "")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            date_str = datetime.now().date().isoformat()

        try:
            case = process_case(
                link_url=link_url,
                title=title,
                date_str=date_str,
                bureau_name=bureau.name_en,
                case_type=case_type,
                skip_fund_check=is_fund_by_title(title) is True,
            )
        except Exception as exc:
            return "error", None, f"{type(exc).__name__}: {exc}"

        if case is None:
            return "skipped", None, ""
        return ("success" if case.has_text else "failed"), case, case.error

    def handle(status: str, case: CaseData | None, error: str) -> None:
        if status == "success" and case is not None:
            store.cases.upsert(case.to_case_record())
            counts["success"] += 1
            return
        if status == "skipped":
            counts["skipped"] += 1
            return
        if status == "error":
            logger.error("  处理异常: %s", error)
        if case is not None:
            store.cases.upsert(case.to_case_record())
        counts["failed"] += 1

    if workers <= 1:
        for index, link_info in enumerate(to_process, 1):
            progress(f"  [{index}/{total}] {link_info.get('title', '')}")
            if on_progress:
                on_progress(index, total, link_info.get("title", ""))
            handle(*worker(link_info))
            if index < total:
                time.sleep(DELAY_BETWEEN_CASES)
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="csrc") as pool:
            futures = {pool.submit(worker, item): item for item in to_process}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    status, case, error = future.result()
                except Exception as exc:
                    status, case, error = "error", None, str(exc)
                handle(status, case, error)
                completed += 1
                if on_progress:
                    on_progress(completed, total, item.get("title", ""))
                if completed % 20 == 0 or completed == total:
                    progress(
                        f"  进度: {completed}/{total} "
                        f"(成功 {counts['success']}, 失败 {counts['failed']}, "
                        f"跳过 {counts['skipped']})"
                    )

    logger.info(
        "  %s/%s: 成功 %d，失败 %d，跳过 %d",
        bureau.name_cn,
        CASE_TYPE_CN[case_type],
        counts["success"],
        counts["failed"],
        counts["skipped"],
    )
    return counts["success"], counts["failed"], counts["skipped"]


def _case_id_of(link_info: dict[str, Any], bureau: Bureau, case_type: str) -> str:
    from .models import build_case_id

    return build_case_id(link_info.get("date", ""), link_info.get("link_url", ""))


def _minimal_record(
    link_info: dict[str, Any],
    bureau: Bureau,
    case_type: str,
    status: CaseStatus,
    note: str,
) -> CaseRecord:
    """为「未抓正文」的案例（如基金不相关）建一条占位记录。"""
    return CaseRecord(
        dataset=Dataset.CSRC,
        case_id=_case_id_of(link_info, bureau, case_type),
        source_url=link_info.get("link_url", ""),
        title=link_info.get("title", ""),
        date=link_info.get("date", ""),
        status=status,
        status_note=note,
        case_type=case_type,
        bureau=bureau.name_en,
    )


# ──────────────────────────── 对外抓取 API ────────────────────────────


@dataclass
class FetchResult:
    """一次抓取任务的汇总结果。"""

    dataset: str = "csrc"
    success: int = 0
    failed: int = 0
    skipped: int = 0
    detail: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.success + self.failed + self.skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "total": self.total,
            "detail": self.detail,
        }


def available_bureaus() -> list[dict[str, str]]:
    """返回全部来源（会本部 + 派出机构）的中英文标识，供界面下拉选择。"""
    return [{"name_en": item.name_en, "name_cn": item.name_cn} for item in BUREAUS]


def default_start_date() -> date:
    """默认抓取起始日期（覆盖 2022 年以来的案例）。"""
    return DEFAULT_START_DATE


def fetch(
    start_date: date | None = None,
    end_date: date | None = None,
    bureaus: list[str] | None = None,
    case_types: list[str] | None = None,
    *,
    store: Any = None,
    concurrency: int | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """抓取指定日期范围、来源与类型的 CSRC 案例。

    Args:
        start_date: 起始日期（含），默认 2022-01-01。
        end_date: 结束日期（含），默认今天。
        bureaus: 证监局英文标识列表，如 ``["HQ", "Beijing"]``；默认全部来源。
        case_types: ``penalty`` / ``measure``；默认两者都抓。
        store: 数据仓储门面（:class:`~regwatch.db.DataStore`）。
        concurrency: 详情页并发线程数。
        on_progress: 进度回调 ``(已处理, 总数, 标题)``。
    """
    if store is None:
        from ...services import get_services

        store = get_services().store

    start_date = start_date or DEFAULT_START_DATE
    end_date = end_date or datetime.now().date()

    selected: list[Bureau] = []
    for name in bureaus or []:
        item = get_bureau_by_name(name)
        if item is None:
            logger.warning("忽略未知证监局标识：%s", name)
        else:
            selected.append(item)
    if not selected:
        selected = list(BUREAUS)

    selected_types = [item for item in (case_types or []) if item in CASE_TYPE_CN]
    if not selected_types:
        selected_types = [CASE_TYPE_PENALTY, CASE_TYPE_MEASURE]

    workers = int(concurrency or DEFAULT_CONCURRENCY)
    result = FetchResult()
    started = time.time()
    logger.info("=" * 20 + " CSRC 案例抓取 " + "=" * 20)
    logger.info(
        "日期范围: %s ~ %s | 来源 %d 个 | 类型: %s | 并发: %d",
        start_date,
        end_date,
        len(selected),
        "、".join(CASE_TYPE_CN[item] for item in selected_types),
        workers,
    )

    try:
        for bureau in selected:
            for case_type in selected_types:
                success, failed, skipped = fetch_source(
                    bureau,
                    case_type,
                    start_date,
                    end_date,
                    store=store,
                    concurrency=workers,
                    on_progress=on_progress,
                )
                result.success += success
                result.failed += failed
                result.skipped += skipped
                result.detail[f"{bureau.name_en}/{case_type}"] = {
                    "success": success,
                    "failed": failed,
                    "skipped": skipped,
                }
    finally:
        close_shared_session()
        store.touch()

    logger.info(
        "CSRC 抓取完成：成功 %d，失败 %d，跳过非基金 %d，耗时 %.1f 秒",
        result.success,
        result.failed,
        result.skipped,
        time.time() - started,
    )
    return result


def fetch_pending(
    bureaus: list[str] | None = None,
    case_types: list[str] | None = None,
    *,
    store: Any = None,
    concurrency: int | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """断点续传：重新扫描区间内所有来源，跳过库里已有正文的案例。"""
    logger.info("断点续传模式：扫描区间内来源，已有正文的案例自动跳过")
    return fetch(
        start_date=DEFAULT_START_DATE,
        end_date=datetime.now().date(),
        bureaus=bureaus,
        case_types=case_types,
        store=store,
        concurrency=concurrency,
        on_progress=on_progress,
    )


def fetch_single(
    url: str,
    bureau: str = "HQ",
    case_type: str = CASE_TYPE_PENALTY,
    *,
    store: Any = None,
) -> CaseData | None:
    """抓取单个案例 URL 并入库（视为已知基金相关，跳过内容确认）。"""
    if store is None:
        from ...services import get_services

        store = get_services().store

    try:
        case = process_case(
            link_url=url,
            title="手动输入案例",
            date_str=datetime.now().date().isoformat(),
            bureau_name=bureau,
            case_type=case_type,
            skip_fund_check=True,
        )
        if case is not None:
            store.cases.upsert(case.to_case_record())
            store.touch()
        return case
    finally:
        close_shared_session()


def case_types_of(value: str) -> CaseType:  # pragma: no cover - 便捷转换
    """把字符串解析为 :class:`~regwatch.domain.CaseType`。"""
    return CaseType.parse(value, CaseType.UNKNOWN) or CaseType.UNKNOWN
