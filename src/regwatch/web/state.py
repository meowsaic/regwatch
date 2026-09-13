"""网页端会话状态集中管理。

原先各视图各自读写 ``st.session_state``（19 处散落），默认值与重置逻辑重复。
这里定义唯一的前缀与读写函数，视图只通过 :func:`get` / :func:`set_value` 访问。
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from regwatch.domain import CaseQuery

__all__ = [
    "FILTER_KEYS",
    "FILTER_WIDGET_KEYS",
    "active_filter_chips",
    "build_query",
    "get",
    "reset_filters",
    "set_value",
]

#: 会话状态的键前缀，避免与 Streamlit 内部键冲突
PREFIX = "regwatch."

#: 筛选相关的键（重置时统一清理）
FILTER_KEYS: tuple[str, ...] = (
    "datasets",
    "statuses",
    "case_types",
    "bureaus",
    "violations",
    "entity_types",
    "date_from",
    "date_to",
    "keyword",
    "fund_related_only",
    "page",
)

#: 筛选控件的 Streamlit widget key。
#: 清除筛选时必须一并 pop：无 key 的控件在 rerun 后会把旧选择写回 session。
FILTER_WIDGET_KEYS: tuple[str, ...] = (
    "cases-datasets",
    "cases-violations",
    "cases-statuses",
    "cases-case-types",
    "cases-bureaus",
    "cases-entity-types",
    "cases-date-from",
    "cases-date-to",
    "cases-keyword",
    "stats-date-from",
    "stats-date-to",
)

#: 各键的默认值。
#: ``statuses`` 默认排除「判定非基金相关」（skipped）——这些案例是采集期
#: 确定性排除或模型精判为与基金无关的占位记录，计入只会稀释统计口径；
#: 需要查看时在筛选器里手动勾选「已跳过」即可。
DEFAULTS: dict[str, Any] = {
    "datasets": [],
    "statuses": ["done", "pending"],
    "case_types": [],
    "bureaus": [],
    "violations": [],
    "entity_types": [],
    "date_from": "",
    "date_to": "",
    "keyword": "",
    "fund_related_only": False,
    "page": 1,
}


def _key(name: str) -> str:
    return f"{PREFIX}{name}"


def get(name: str, default: Any = None) -> Any:
    """读取会话状态；未设置时返回默认值。"""
    key = _key(name)
    if key not in st.session_state:
        st.session_state[key] = DEFAULTS.get(name, default)
    return st.session_state[key]


def set_value(name: str, value: Any) -> None:
    """写入会话状态。"""
    st.session_state[_key(name)] = value


def reset_filters() -> None:
    """清空全部筛选条件（含控件内部状态）。"""
    for name in FILTER_KEYS:
        st.session_state[_key(name)] = DEFAULTS.get(name)
    for widget_key in FILTER_WIDGET_KEYS:
        st.session_state.pop(widget_key, None)


def build_query(*, limit: int = 0, offset: int = 0, order_by: str = "date") -> CaseQuery:
    """按当前会话状态构造查询条件（CLI 与网页共用的唯一筛选真源）。"""
    from regwatch.domain import CaseStatus, CaseType, Category, Dataset

    def tuple_of(name: str, parser: Any) -> tuple:
        values = get(name, []) or []
        parsed = [parser(value) for value in values]
        return tuple(item for item in parsed if item)

    return CaseQuery(
        datasets=tuple_of("datasets", Dataset.parse) or Dataset.all(),
        statuses=tuple_of("statuses", CaseStatus.parse),
        case_types=tuple_of("case_types", CaseType.parse),
        categories=tuple_of("categories", Category.parse),
        violations=tuple(get("violations", []) or []),
        bureaus=tuple(get("bureaus", []) or []),
        entity_types=tuple(get("entity_types", []) or []),
        date_from=get("date_from", "") or None,
        date_to=get("date_to", "") or None,
        keyword=get("keyword", ""),
        fund_related_only=bool(get("fund_related_only", False)),
        limit=limit,
        offset=offset,
        order_by=order_by,
    )


def active_filter_chips() -> list[str]:
    """把当前会话筛选条件压成摘要 chips（案例浏览与统计分析共用）。

    统计分析页只暴露日期控件，但查询条件与案例浏览同源；
    展示这些「隐形筛选」可以避免「日期范围很大却只有几例」的困惑。
    """
    from regwatch.domain import CaseStatus, CaseType, Dataset

    chips: list[str] = []
    datasets = get("datasets") or []
    if datasets:
        labels = []
        for value in datasets:
            parsed = Dataset.parse(value)
            labels.append(parsed.label if parsed else str(value))
        chips.append("数据集：" + "、".join(labels))
    case_types = get("case_types") or []
    if case_types:
        labels = []
        for value in case_types:
            parsed = CaseType.parse(value)
            labels.append(parsed.label if parsed else str(value))
        chips.append("案例类型：" + "、".join(labels))
    statuses = get("statuses") or []
    if statuses and sorted(statuses) != sorted(DEFAULTS["statuses"]):
        # 与默认口径一致时不提示，避免每页都挂着一条恒定的状态 chip
        labels = []
        for value in statuses:
            parsed = CaseStatus.parse(value)
            labels.append(parsed.label if parsed else str(value))
        chips.append("状态：" + "、".join(labels))
    violations = get("violations") or []
    if violations:
        shown = list(violations)[:3]
        extra = len(violations) - len(shown)
        suffix = f" +{extra}" if extra > 0 else ""
        chips.append("违规：" + "、".join(shown) + suffix)
    bureaus = get("bureaus") or []
    if bureaus:
        shown = list(bureaus)[:3]
        extra = len(bureaus) - len(shown)
        suffix = f" +{extra}" if extra > 0 else ""
        chips.append("来源局：" + "、".join(shown) + suffix)
    entity_types = get("entity_types") or []
    if entity_types:
        chips.append("主体：" + "、".join(entity_types))
    date_from = get("date_from") or ""
    date_to = get("date_to") or ""
    if date_from or date_to:
        chips.append(f"日期：{date_from or '不限'} ~ {date_to or '不限'}")
    keyword = str(get("keyword") or "").strip()
    if keyword:
        chips.append(f"关键词：{keyword}")
    return chips
