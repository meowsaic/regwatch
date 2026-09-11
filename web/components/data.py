"""网页端数据加载层（带 Streamlit 缓存）。

缓存策略：

- 以 :func:`regwatch.storage.catalog_signature` 作为缓存键，数据变化后自动失效；
- 列表与统计只读结构化字段，**不载入案例正文**；
- 正文在用户展开详情时通过 :func:`load_case_text` 按需读取。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import streamlit as st

_WEB_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _WEB_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.analyze import analyze
from regwatch.storage import (
    DATASETS,
    CaseRow,
    build_all_catalogs,
    build_catalog,
    catalog_signature,
    filter_rows,
    read_case,
    read_json,
)

__all__ = [
    "clamp_page",
    "clear_data_cache",
    "filter_case_dicts",
    "load_case_payload",
    "load_case_text",
    "load_rows",
    "load_stats",
    "violation_options",
]


def clamp_page(value: object, page_count: int) -> int:
    """把页码钳制到 ``[1, page_count]``；非法或缺失时返回 1。"""
    count = max(1, int(page_count))
    try:
        page = int(value) if value is not None else 1
    except (TypeError, ValueError):
        return 1
    return max(1, min(page, count))


@st.cache_data(ttl=300, show_spinner="正在载入案例清单…")
def _load_catalog_cached(
    datasets: tuple[str, ...], signature: tuple[Any, ...]
) -> list[dict[str, Any]]:
    rows: list[CaseRow] = []
    for dataset in datasets:
        rows.extend(build_catalog(dataset))
    rows.sort(key=lambda row: (row.date, row.case_id), reverse=True)
    return [row.to_dict() for row in rows]


@st.cache_data(ttl=300, show_spinner="正在计算统计数据…")
def _load_stats_cached(
    datasets: tuple[str, ...],
    signature: tuple[Any, ...],
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    rows: list[CaseRow] = []
    for dataset in datasets:
        rows.extend(build_catalog(dataset))
    rows.sort(key=lambda row: (row.date, row.case_id), reverse=True)
    if date_from or date_to:
        rows = filter_rows(rows, date_from=date_from, date_to=date_to)
    result = analyze(rows)
    payload = result.to_json_payload()
    payload["comparison"] = result.stats["comparison"]
    payload["typical"] = {
        name: [case.to_dict() for case in cases]
        for name, cases in result.stats["representative"].items()
    }
    return payload


def _select(datasets: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(ds for ds in datasets if ds in DATASETS)
    return selected or tuple(DATASETS)


def load_rows(datasets: Sequence[str] = DATASETS, force: bool = False) -> list[dict[str, Any]]:
    """载入案例清单（字典形式，不含正文）。"""
    selected = _select(datasets)
    if force:
        build_all_catalogs(force=True)
        _load_catalog_cached.clear()
    return _load_catalog_cached(selected, catalog_signature(selected))


def load_stats(
    datasets: Sequence[str] = DATASETS,
    date_from: str = "",
    date_to: str = "",
) -> dict[str, Any]:
    """载入统计聚合结果（分布、对比、趋势、代表案例）。

    ``date_from`` / ``date_to`` 为 ``YYYY-MM-DD``（含边界），可单独使用。
    """
    selected = _select(datasets)
    return _load_stats_cached(selected, catalog_signature(selected), date_from or "", date_to or "")


def load_case_text(case: dict[str, Any]) -> str:
    """按需读取案例正文。"""
    path = str(case.get("case_file") or "")
    if path and Path(path).exists():
        data = read_json(Path(path))
        return str((data or {}).get("raw_text", ""))
    data = read_case(
        str(case.get("dataset", "")),
        str(case.get("case_id", "")),
        bureau=str(case.get("bureau", "")),
        case_type=str(case.get("case_type", "")),
        category=str(case.get("category", "")),
    )
    return str((data or {}).get("raw_text", ""))


def load_case_payload(case: dict[str, Any]) -> dict[str, Any] | None:
    """读取案例完整 JSON（含全部原始字段）。"""
    path = str(case.get("case_file") or "")
    if path and Path(path).exists():
        return read_json(Path(path))
    return read_case(
        str(case.get("dataset", "")),
        str(case.get("case_id", "")),
        bureau=str(case.get("bureau", "")),
        case_type=str(case.get("case_type", "")),
        category=str(case.get("category", "")),
    )


def filter_case_dicts(
    rows: Sequence[dict[str, Any]],
    datasets: Sequence[str] = (),
    entity_types: Sequence[str] = (),
    violation_types: Sequence[str] = (),
    statuses: Sequence[str] = (),
    bureaus: Sequence[str] = (),
    case_types: Sequence[str] = (),
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """对字典清单做多维筛选（语义与 :func:`regwatch.storage.filter_rows` 一致）。"""
    dataset_set = set(datasets)
    entity_set = set(entity_types)
    violation_set = set(violation_types)
    status_set = set(statuses)
    bureau_set = set(bureaus)
    case_type_set = set(case_types)
    needle = (keyword or "").strip().lower()

    out: list[dict[str, Any]] = []
    for item in rows:
        if dataset_set and item.get("dataset") not in dataset_set:
            continue
        if entity_set and item.get("entity_type") not in entity_set:
            continue
        if status_set and item.get("status") not in status_set:
            continue
        if bureau_set and item.get("bureau") not in bureau_set:
            continue
        if case_type_set and item.get("case_type") not in case_type_set:
            continue
        if violation_set and not (violation_set & set(item.get("violation_types") or [])):
            continue
        date = str(item.get("date") or "")
        if date_from and date and date < date_from:
            continue
        if date_to and date and date > date_to:
            continue
        if needle:
            haystack = " ".join(
                str(item.get(key, ""))
                for key in ("title", "entity", "violation_summary", "involved_fund", "case_id")
            ).lower()
            if needle not in haystack:
                continue
        out.append(item)
        if limit is not None and len(out) >= limit:
            break
    return out


@st.cache_data(ttl=300, show_spinner=False)
def violation_options() -> list[str]:
    """违规类型下拉选项（分类体系 + 数据中实际出现的类型）。"""
    from regwatch.prompts import VIOLATION_TYPES

    seen = list(VIOLATION_TYPES)
    for row in build_all_catalogs():
        for item in row.violation_types:
            if item not in seen:
                seen.append(item)
    return seen


def clear_data_cache() -> None:
    """手动清空数据缓存（任务完成后调用）。"""
    _load_catalog_cached.clear()
    _load_stats_cached.clear()
    violation_options.clear()
