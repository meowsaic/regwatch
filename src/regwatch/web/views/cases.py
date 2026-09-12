"""案例浏览：多维筛选、分页列表与正文详情。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from regwatch.domain import CaseStatus, CaseType, Dataset, violation_label, violations_text
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

_PLACEHOLDER = "请选择…"
_EMPTY = "—"


def render() -> None:
    ui.page_header("案例浏览", "按数据集、主体、违规类型、来源局与日期组合筛选")

    _render_filters()

    query = build_query()
    all_rows = load_rows(cache_key(query))
    ui.filter_summary(len(all_rows), _active_filter_chips())
    if not all_rows:
        ui.empty_state("没有符合条件的案例", "试试放宽筛选条件或清空关键词。")
        return

    page_count = max(1, (len(all_rows) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = clamp_page(get("page", 1), page_count)
    set_value("page", page)
    start = (page - 1) * _PAGE_SIZE
    page_rows = all_rows[start : start + _PAGE_SIZE]

    frame = _rows_frame(all_rows)
    page_frame = frame.iloc[start : start + _PAGE_SIZE].reset_index(drop=True)

    toolbar_left, toolbar_right = st.columns([3, 2])
    with toolbar_left:
        st.caption(f"共 {len(all_rows):,} 条，第 {page}/{page_count} 页")
    with toolbar_right:
        ui.download_button(
            frame,
            filename="regwatch-cases-filtered.csv",
            label=f"导出当前筛选结果（{len(all_rows):,} 条）",
            key="cases-export-csv",
        )

    selection = st.dataframe(
        page_frame,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "日期": st.column_config.TextColumn("日期", width="small"),
            "来源": st.column_config.TextColumn("来源", width="small"),
            "标题": st.column_config.TextColumn("标题", width="large"),
            "当事人": st.column_config.TextColumn("当事人", width="medium"),
            "违规类型": st.column_config.TextColumn("违规类型", width="medium"),
            "处罚": st.column_config.TextColumn("处罚", width="medium"),
            "状态": st.column_config.TextColumn("状态", width="small"),
        },
    )
    _render_pager(page, page_count)

    chosen = _selected_index(selection)
    if chosen is None:
        ui.callout("在表格中点选一行即可查看案例详情与决定书原文。")
        return
    _render_detail(page_rows[chosen])


def _violation_text(row: dict) -> str:
    """案例行的违规类型展示串（归一后按数据集措辞，去掉模型输出的碎片）。"""
    return violations_text(row.get("violation_type"), row.get("dataset")) or _EMPTY


def _violation_format(value: str) -> str:
    """违规类型下拉的展示文案：限定单数据集筛选时用该数据集的措辞。"""
    datasets = get("datasets") or []
    if len(datasets) == 1:
        return violation_label(value, datasets[0])
    return value


def _rows_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": row["date"] or _EMPTY,
                "来源": row["dataset_label"] or _EMPTY,
                "标题": row["title"] or _EMPTY,
                "当事人": row["punished_entities"] or _EMPTY,
                "违规类型": _violation_text(row),
                "处罚": row["punishment"] or _EMPTY,
                "状态": row["status_label"] or _EMPTY,
            }
            for row in rows
        ]
    )


def _active_filter_chips() -> list[str]:
    """把当前会话筛选条件压成摘要 chips。"""
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
    if statuses:
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


def _multiselect(label: str, **kwargs: Any) -> list:
    """统一中文 placeholder 的多选控件（供 ``field`` 的 render 回调使用）。"""
    kwargs.setdefault("placeholder", _PLACEHOLDER)
    return st.multiselect(
        label,
        label_visibility="collapsed",
        **kwargs,
    )


def _render_filters() -> None:
    with st.expander("筛选条件", expanded=True):
        pair1, pair2 = st.columns(2)
        with pair1:
            set_value(
                "datasets",
                ui.field(
                    "数据集",
                    lambda: _multiselect(
                        "数据集",
                        options=[item.value for item in Dataset.all()],
                        format_func=lambda value: (Dataset.parse(value) or Dataset.AMAC).label,
                        default=get("datasets", []),
                        key="cases-datasets",
                    ),
                ),
            )
            set_value(
                "violations",
                ui.field(
                    "违规类型",
                    lambda: _multiselect(
                        "违规类型",
                        options=violation_options(),
                        format_func=_violation_format,
                        default=get("violations", []),
                        key="cases-violations",
                    ),
                ),
            )
            set_value(
                "statuses",
                ui.field(
                    "处理状态",
                    lambda: _multiselect(
                        "处理状态",
                        options=[item.value for item in CaseStatus],
                        format_func=lambda value: (
                            (CaseStatus.parse(value) or CaseStatus.PENDING).label
                        ),
                        default=get("statuses", []),
                        key="cases-statuses",
                    ),
                ),
            )
            ui.field(
                "起始日期",
                lambda: ui.date_input_field(
                    "起始日期",
                    "date_from",
                    widget_key="cases-date-from",
                ),
            )
        with pair2:
            set_value(
                "case_types",
                ui.field(
                    "案例类型",
                    lambda: _multiselect(
                        "案例类型（CSRC）",
                        options=[CaseType.PENALTY.value, CaseType.MEASURE.value],
                        format_func=lambda value: (CaseType.parse(value) or CaseType.UNKNOWN).label,
                        default=get("case_types", []),
                        key="cases-case-types",
                    ),
                    hint="CSRC",
                ),
            )
            set_value(
                "bureaus",
                ui.field(
                    "来源局",
                    lambda: _multiselect(
                        "来源局（CSRC）",
                        options=bureau_options(),
                        default=get("bureaus", []),
                        key="cases-bureaus",
                    ),
                    hint="CSRC",
                ),
            )
            set_value(
                "entity_types",
                ui.field(
                    "主体类型",
                    lambda: _multiselect(
                        "主体类型",
                        options=["机构", "个人", "机构+个人"],
                        default=get("entity_types", []),
                        key="cases-entity-types",
                    ),
                ),
            )
            ui.field(
                "结束日期",
                lambda: ui.date_input_field(
                    "结束日期",
                    "date_to",
                    widget_key="cases-date-to",
                ),
            )

        set_value(
            "keyword",
            ui.field(
                "关键词",
                lambda: st.text_input(
                    "关键词（标题 / 当事人 / 摘要）",
                    value=get("keyword", ""),
                    placeholder="标题 / 当事人 / 摘要…",
                    label_visibility="collapsed",
                    key="cases-keyword",
                ),
                hint="标题 / 当事人 / 摘要",
            ),
        )

        if st.button("清除筛选"):
            reset_filters()
            st.rerun()


def _render_pager(page: int, page_count: int) -> None:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("上一页", disabled=page <= 1, width="stretch"):
            set_value("page", page - 1)
            st.rerun()
    with col2:
        st.markdown(
            f"<div style='text-align:center;padding-top:.45rem;color:#64748B;font-size:14px'>"
            f"第 {page} / {page_count} 页</div>",
            unsafe_allow_html=True,
        )
    with col3:
        if st.button("下一页", disabled=page >= page_count, width="stretch"):
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
    source_html = ui.source_link(row.get("source_url"), label="打开监管原文")
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
                ("来源链接", source_html),
            ],
            raw_keys={"来源链接"},
        ),
        unsafe_allow_html=True,
    )

    if row.get("violation_summary"):
        ui.section_header("违规事实摘要")
        st.write(row["violation_summary"])

    with st.expander("查看决定书原文", expanded=False):
        text = load_case_text(row["dataset"], row["case_id"])
        if text:
            st.text_area("正文", text, height=400, label_visibility="collapsed")
        else:
            ui.empty_state("该案例没有正文", "可能抓取失败或正文为空。")
