"""把 :class:`~regwatch.domain.CaseQuery` 编译成 SQL 片段。

CLI 与网页端共用同一份筛选条件，因此这里也是「筛选语义」的唯一实现处，
避免历史上 ``storage.filter_rows`` 与 ``web.filter_case_dicts`` 两套并行的情况。
"""

from __future__ import annotations

from typing import Any

from ...domain import CaseQuery

__all__ = ["ORDER_COLUMNS", "build_order_by", "build_where", "escape_like"]

#: 允许排序的列白名单（防止 SQL 注入）
ORDER_COLUMNS: dict[str, str] = {
    "date": "c.date",
    "title": "c.title",
    "dataset": "c.dataset",
    "bureau": "c.bureau",
    "status": "c.status",
    "updated_at": "c.updated_at",
    "case_id": "c.case_id",
}

#: 允许 SQL 聚合分组的列白名单
GROUP_COLUMNS: dict[str, str] = {
    "dataset": "c.dataset",
    "status": "c.status",
    "bureau": "c.bureau",
    "case_type": "c.case_type",
    "category": "c.category",
    "entity_type": "s.entity_type",
    "date": "c.date",
    "org_type": "c.org_type",
}

_LIKE_ESCAPES = (("\\", "\\\\"), ("%", "\\%"), ("_", "\\_"))


def escape_like(text: str) -> str:
    """转义 LIKE 通配符，使关键词按字面匹配。"""
    for old, new in _LIKE_ESCAPES:
        text = text.replace(old, new)
    return text


def _in_clause(column: str, values: tuple[Any, ...]) -> tuple[str, list[Any]]:
    placeholders = ", ".join("?" for _ in values)
    return f"{column} IN ({placeholders})", [str(value) for value in values]


def build_where(query: CaseQuery) -> tuple[str, list[Any]]:
    """生成 ``WHERE`` 子句与参数列表（返回 ``("1=1", [])`` 表示不限制）。"""
    clauses: list[str] = []
    params: list[Any] = []

    if query.datasets:
        clause, values = _in_clause("c.dataset", query.datasets)
        clauses.append(clause)
        params.extend(values)

    if query.statuses:
        clause, values = _in_clause("c.status", query.statuses)
        clauses.append(clause)
        params.extend(values)

    if query.case_types:
        clause, values = _in_clause("c.case_type", query.case_types)
        clauses.append(clause)
        params.extend(values)

    if query.categories:
        clause, values = _in_clause("c.category", query.categories)
        clauses.append(clause)
        params.extend(values)

    if query.bureaus:
        clause, values = _in_clause("c.bureau", query.bureaus)
        clauses.append(clause)
        params.extend(values)

    if query.entity_types:
        clause, values = _in_clause("s.entity_type", query.entity_types)
        clauses.append(clause)
        params.extend(values)

    if query.violations:
        placeholders = ", ".join("?" for _ in query.violations)
        clauses.append(
            "EXISTS (SELECT 1 FROM case_violations v WHERE v.dataset = c.dataset "
            f"AND v.case_id = c.case_id AND v.violation IN ({placeholders}))"
        )
        params.extend(query.violations)

    # 空日期视为「不限」，与旧版内存筛选行为一致
    if query.date_from:
        clauses.append("(c.date = '' OR c.date >= ?)")
        params.append(query.date_from)
    if query.date_to:
        clauses.append("(c.date = '' OR c.date <= ?)")
        params.append(query.date_to)

    if query.fund_related_only:
        clauses.append("c.is_fund_related = 1")

    keyword = (query.keyword or "").strip()
    if keyword:
        needle = f"%{escape_like(keyword.lower())}%"
        like = "LIKE ? ESCAPE '\\'"
        fields = [
            "lower(c.title)",
            "lower(c.punished_entities)",
            "lower(c.punished_entity)",
            "lower(c.case_id)",
            "lower(c.document_number)",
            "lower(COALESCE(s.violation_summary, ''))",
            "lower(COALESCE(s.involved_fund, ''))",
        ]
        clauses.append("(" + " OR ".join(f"{field} {like}" for field in fields) + ")")
        params.extend([needle] * len(fields))

    return (" AND ".join(clauses) if clauses else "1=1"), params


def build_order_by(query: CaseQuery) -> str:
    """生成 ``ORDER BY`` 子句（含稳定次级排序）。"""
    column = ORDER_COLUMNS.get(query.order_by, ORDER_COLUMNS["date"])
    direction = "DESC" if query.descending else "ASC"
    return f"{column} {direction}, c.case_id {direction}"
