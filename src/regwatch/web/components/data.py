"""网页端数据加载层（带 Streamlit 缓存）。

缓存键由两部分构成：数据版本号（``db revision``）+ 查询条件，
因此任何写入都会让缓存自动失效，而无需再扫描目录 mtime。

业务一律走 :mod:`regwatch.services`；本模块只做缓存与字典化，
不再自己实现一套筛选（历史上 ``filter_case_dicts`` 与 ``storage.filter_rows``
两份并行，现已统一到 :class:`~regwatch.domain.CaseQuery`）。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from regwatch.domain import CaseQuery, CaseStatus, Dataset
from regwatch.services import Services, get_services
from regwatch.settings import Settings, get_settings

__all__ = [
    "bureau_options",
    "clamp_page",
    "clear_data_cache",
    "load_case_text",
    "load_overview",
    "load_rows",
    "load_stats",
    "services",
    "settings",
    "status_options",
    "violation_options",
]


@st.cache_resource(show_spinner=False)
def services() -> Services:
    """进程内共享的服务门面（含数据库连接）。"""
    return get_services()


def settings() -> Settings:
    return get_settings()


def _revision() -> int:
    return services().store.revision()


def clamp_page(value: object, page_count: int) -> int:
    """把页码钳制到 ``[1, page_count]``；非法或缺失时返回 1。"""
    count = max(1, int(page_count))
    try:
        page = int(value) if value is not None else 1
    except (TypeError, ValueError):
        return 1
    return max(1, min(page, count))


def cache_key(query: CaseQuery) -> tuple:
    """把查询条件转成可哈希的缓存键。"""
    return (
        tuple(sorted(item.value for item in query.datasets)),
        tuple(sorted(item.value for item in query.statuses)),
        tuple(sorted(item.value for item in query.case_types)),
        tuple(sorted(item.value for item in query.categories)),
        tuple(sorted(query.violations)),
        tuple(sorted(query.bureaus)),
        tuple(sorted(query.entity_types)),
        query.date_from or "",
        query.date_to or "",
        query.keyword or "",
        query.fund_related_only,
        query.limit,
        query.offset,
        query.order_by,
        query.descending,
    )


@st.cache_data(ttl=300, show_spinner="正在载入案例…")
def load_rows(query_key: tuple) -> list[dict[str, Any]]:
    """按查询条件载入案例行（字典形式，不含正文）。"""
    query = _query_from_key(query_key)
    return [row.to_dict() for row in services().analysis.rows(query)]


@st.cache_data(ttl=300, show_spinner="正在计算统计数据…")
def load_stats(query_key: tuple) -> dict[str, Any]:
    """按查询条件聚合统计。"""
    query = _query_from_key(query_key)
    analysis = services().analysis
    result = analysis.analyze_query(query)
    payload = result.to_json_payload()
    payload["comparison"] = result.stats["comparison"]
    payload["typical"] = {
        name: [case.to_dict() for case in cases]
        for name, cases in result.stats["representative"].items()
    }
    return payload


@st.cache_data(ttl=60, show_spinner=False)
def load_overview(query_key: tuple) -> dict[str, Any]:
    """纯 SQL 聚合的看板指标。"""
    return services().analysis.overview(_query_from_key(query_key))


@st.cache_data(ttl=300, show_spinner=False)
def violation_options() -> list[str]:
    """违规类型下拉选项（canonical 分类体系 + 库内实际出现的类型）。

    选项值统一为 canonical 名，展示文案由调用方按数据集映射
    （见 :func:`regwatch.domain.violation_label`）。
    """
    from regwatch.domain import VIOLATION_TYPES

    seen = list(VIOLATION_TYPES)
    for item in services().summaries.violation_options():
        if item not in seen:
            seen.append(item)
    return seen


@st.cache_data(ttl=300, show_spinner=False)
def bureau_options() -> list[str]:
    """来源局下拉选项。"""
    rows = services().store.db.query(
        "SELECT DISTINCT bureau FROM cases WHERE bureau <> '' ORDER BY bureau"
    )
    return [str(row["bureau"]) for row in rows]


def status_options() -> list[str]:
    """案例状态下拉选项。"""
    return [status.value for status in CaseStatus]


def load_case_text(dataset: str, case_id: str) -> str:
    """按需读取案例正文（不进缓存，避免占内存）。"""
    target = Dataset.parse(dataset)
    if target is None:
        return ""
    return services().cases.get_body(target, case_id)


def clear_data_cache() -> None:
    """手动清空数据缓存（任务完成后调用）。"""
    load_rows.clear()
    load_stats.clear()
    load_overview.clear()
    violation_options.clear()
    bureau_options.clear()


def _query_from_key(query_key: tuple) -> CaseQuery:
    """把缓存键还原为 :class:`CaseQuery`。

    缓存键与查询条件一一对应，这里按 :func:`_query_key` 的字段顺序重建。
    """
    (
        datasets,
        statuses,
        case_types,
        categories,
        violations,
        bureaus,
        entity_types,
        date_from,
        date_to,
        keyword,
        fund_related_only,
        limit,
        offset,
        order_by,
        descending,
    ) = query_key
    return CaseQuery(
        datasets=tuple(item for item in (Dataset.parse(v) for v in datasets) if item),
        statuses=tuple(item for item in (CaseStatus.parse(v) for v in statuses) if item),
        case_types=tuple(item for item in (_parse_case_type(v) for v in case_types) if item),
        categories=tuple(item for item in (_parse_category(v) for v in categories) if item),
        violations=violations,
        bureaus=bureaus,
        entity_types=entity_types,
        date_from=date_from or None,
        date_to=date_to or None,
        keyword=keyword,
        fund_related_only=fund_related_only,
        limit=limit,
        offset=offset,
        order_by=order_by,
        descending=descending,
    )


def _parse_case_type(value: str):
    from regwatch.domain import CaseType

    parsed = CaseType.parse(value)
    return parsed if parsed not in (None, CaseType.UNKNOWN) else None


def _parse_category(value: str):
    from regwatch.domain import Category

    parsed = Category.parse(value)
    return parsed if parsed not in (None, Category.UNKNOWN) else None
