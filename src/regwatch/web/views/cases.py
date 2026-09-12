"""案例浏览：多维筛选、分页列表与正文详情。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from regwatch.domain import CaseStatus, CaseType, Dataset
from regwatch.web.components import ui
from regwatch.web.components.data import (
    bureau_options,
    cache_key,
    clamp_page,
    load_case_text,
    load_rows,
    violation_options,
)
from regwatch.web.state import build_query, get, reset_filters, set_value

__all__ = ["render"]

_PAGE_SIZE = 20


def render() -> None:
    ui.page_header("案例浏览", "按数据集、主体、违规类型、来源局与日期组合筛选")

    _render_filters()

    query = build_query()
    all_rows = load_rows(cache_key(query))
    if not all_rows:
        ui.empty_state("没有符合条件的案例", "试试放宽筛选条件或清空关键词。")
        return

    page_count = max(1, (len(all_rows) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = clamp_page(get("page", 1), page_count)
    set_value("page", page)
    start = (page - 1) * _PAGE_SIZE
    page_rows = all_rows[start : start + _PAGE_SIZE]

    st.caption(f"共 {len(all_rows):,} 条，第 {page}/{page_count} 页")
    frame = pd.DataFrame(
        [
            {
                "日期": row["date"],
                "来源": row["dataset_label"],
                "标题": row["title"],
                "当事人": row["punished_entities"],
                "违规类型": row["violation_type"],
                "处罚": row["punishment"],
                "状态": row["status_label"],
            }
            for row in page_rows
        ]
    )

    selection = st.dataframe(
        frame,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )
    _render_pager(page, page_count)

    chosen = _selected_index(selection)
    if chosen is None:
        ui.callout("在表格中点选一行即可查看案例详情与决定书原文。")
        return
    _render_detail(page_rows[chosen])


def _render_filters() -> None:
    with st.expander("筛选条件", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            set_value(
                "datasets",
                st.multiselect(
                    "数据集",
                    options=[item.value for item in Dataset.all()],
                    format_func=lambda value: (Dataset.parse(value) or Dataset.AMAC).label,
                    default=get("datasets", []),
                ),
            )
            set_value(
                "statuses",
                st.multiselect(
                    "处理状态",
                    options=[item.value for item in CaseStatus],
                    format_func=lambda value: (CaseStatus.parse(value) or CaseStatus.PENDING).label,
                    default=get("statuses", []),
                ),
            )
        with col2:
            set_value(
                "case_types",
                st.multiselect(
                    "案例类型（CSRC）",
                    options=[CaseType.PENALTY.value, CaseType.MEASURE.value],
                    format_func=lambda value: (CaseType.parse(value) or CaseType.UNKNOWN).label,
                    default=get("case_types", []),
                ),
            )
            set_value(
                "bureaus",
                st.multiselect(
                    "来源局（CSRC）", options=bureau_options(), default=get("bureaus", [])
                ),
            )
        with col3:
            set_value(
                "violations",
                st.multiselect(
                    "违规类型", options=violation_options(), default=get("violations", [])
                ),
            )
            set_value(
                "entity_types",
                st.multiselect(
                    "主体类型",
                    options=["机构", "个人", "机构+个人"],
                    default=get("entity_types", []),
                ),
            )

        col4, col5, col6 = st.columns([1, 1, 2])
        with col4:
            set_value("date_from", st.text_input("起始日期", value=get("date_from", "")))
        with col5:
            set_value("date_to", st.text_input("结束日期", value=get("date_to", "")))
        with col6:
            set_value(
                "keyword",
                st.text_input("关键词（标题 / 当事人 / 摘要）", value=get("keyword", "")),
            )

        if st.button("清除筛选"):
            reset_filters()
            st.rerun()


def _render_pager(page: int, page_count: int) -> None:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("上一页", disabled=page <= 1):
            set_value("page", page - 1)
            st.rerun()
    with col2:
        st.markdown(
            f"<div style='text-align:center'>第 {page} / {page_count} 页</div>",
            unsafe_allow_html=True,
        )
    with col3:
        if st.button("下一页", disabled=page >= page_count):
            set_value("page", page + 1)
            st.rerun()


def _selected_index(selection: Any) -> int | None:
    if not isinstance(selection, dict):
        return None
    rows = selection.get("selection", {}).get("rows") or []
    return int(rows[0]) if rows else None


def _render_detail(row: dict) -> None:
    st.markdown("---")
    st.markdown(f"### {row['title'] or '（无标题）'}")
    st.markdown(
        ui.badges(
            [
                (row["dataset_label"], "primary"),
                (row["status_label"], "accent"),
                (row["date"] or "日期未知", "muted"),
            ]
        ),
        unsafe_allow_html=True,
    )
    st.markdown(
        ui.detail_rows(
            [
                ("案例编号", row["case_id"]),
                ("受处分主体", row["punished_entities"]),
                ("主体类型", row["entity_type"]),
                ("违规类型", row["violation_type"]),
                ("处罚措施", row["punishment"]),
                ("涉及基金", row["involved_fund"]),
                ("法律依据", row["legal_basis"]),
                ("文号", row["document_number"]),
                ("来源链接", row["source_url"]),
            ]
        ),
        unsafe_allow_html=True,
    )

    if row.get("violation_summary"):
        st.markdown("**违规事实摘要**")
        st.write(row["violation_summary"])

    with st.expander("查看决定书原文", expanded=False):
        text = load_case_text(row["dataset"], row["case_id"])
        if text:
            st.text_area("正文", text, height=400, label_visibility="collapsed")
        else:
            ui.empty_state("该案例没有正文", "可能抓取失败或正文为空。")
