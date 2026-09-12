"""统计分析：违规分布、处罚构成、机构与个人对比、法规与趋势。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from regwatch.web.components import charts, ui
from regwatch.web.components.data import cache_key, load_stats
from regwatch.web.state import active_filter_chips, build_query, reset_filters

__all__ = ["render"]


def render() -> None:
    ui.page_header("统计分析", "违规类型、处罚措施、主体对比与法规引用")

    with st.expander("统计范围", expanded=False):
        cols = st.columns(2)
        with cols[0]:
            ui.field(
                "起始日期",
                lambda: ui.date_input_field(
                    "起始日期",
                    "date_from",
                    widget_key="stats-date-from",
                ),
            )
        with cols[1]:
            ui.field(
                "结束日期",
                lambda: ui.date_input_field(
                    "结束日期",
                    "date_to",
                    widget_key="stats-date-to",
                ),
            )

    stats = load_stats(cache_key(build_query()))

    # 查询条件与案例浏览共享；日期之外还有筛选时明确提示，避免「数量不对」的困惑
    chips = active_filter_chips()
    if chips:
        hint_cols = st.columns([5, 1])
        with hint_cols[0]:
            st.caption("已应用筛选（与案例浏览共享）：" + "；".join(chips))
        with hint_cols[1]:
            if st.button("清空筛选", key="stats-clear-filters"):
                reset_filters()
                st.rerun()

    total = stats.get("basic", {}).get("total", 0)
    if not total:
        ui.empty_state("暂无可统计的案例", "先运行抓取与摘要提取。")
        return

    st.caption(f"统计口径：共 {total:,} 例；同一案例可计入多个违规类型。")

    tab1, tab2, tab3, tab4 = st.tabs(["违规类型", "处罚措施", "机构与个人", "法规与趋势"])

    with tab1:
        items = stats.get("violation_distribution", [])
        if not items:
            ui.empty_state("暂无违规类型数据", "运行摘要提取后这里会展示分布。")
        else:
            st.plotly_chart(
                charts.hbar(
                    [item["type"] for item in items[:15]],
                    [item["count"] for item in items[:15]],
                    title="违规类型分布 TOP15",
                ),
                width="stretch",
            )
            frame = pd.DataFrame(items)
            st.dataframe(
                frame,
                width="stretch",
                hide_index=True,
                column_config={
                    "type": st.column_config.TextColumn("违规类型", width="large"),
                    "count": st.column_config.NumberColumn("案例数", width="small"),
                },
            )
            ui.download_button(
                frame,
                filename="regwatch-violation-distribution.csv",
                label="导出违规类型分布",
                key="stats-export-violations",
            )

    with tab2:
        categories = stats.get("punishment_categories", [])
        details = stats.get("punishment_details", [])
        if categories:
            st.plotly_chart(
                charts.donut(
                    [item["category"] for item in categories],
                    [item["count"] for item in categories],
                    title="处罚类别构成",
                ),
                width="stretch",
            )
        if details:
            st.dataframe(pd.DataFrame(details), width="stretch", hide_index=True)
        if not categories and not details:
            ui.empty_state("暂无处罚措施数据", "摘要提取完成后可按处罚类别聚合。")

    with tab3:
        comparison = stats.get("entity_comparison", {})
        inst = dict(comparison.get("inst_violations") or [])
        pers = dict(comparison.get("pers_violations") or [])
        names = sorted(
            set(inst) | set(pers),
            key=lambda name: inst.get(name, 0) + pers.get(name, 0),
            reverse=True,
        )[:10]
        if names:
            st.plotly_chart(
                charts.grouped_bar(
                    names,
                    {
                        "机构": [inst.get(name, 0) for name in names],
                        "个人": [pers.get(name, 0) for name in names],
                    },
                    title="机构 vs 个人：违规类型对比",
                ),
                width="stretch",
            )
        else:
            ui.empty_state("暂无主体对比数据", "需要已提取且标注主体类型的案例。")

    with tab4:
        laws = stats.get("legal_basis_top", [])
        trend_rows = stats.get("time_trend", [])
        if laws:
            st.plotly_chart(
                charts.hbar(
                    [item["law"] for item in laws[:15]],
                    [item["count"] for item in laws[:15]],
                    title="法规引用 TOP15",
                ),
                width="stretch",
            )
        if trend_rows:
            st.plotly_chart(
                charts.trend(
                    [row["period"] for row in trend_rows],
                    [row["count"] for row in trend_rows],
                    title="月度趋势",
                ),
                width="stretch",
            )
        if not laws and not trend_rows:
            ui.empty_state("暂无法规与趋势数据")

    typical = stats.get("typical", {})
    if typical:
        ui.section_header("代表案例", "各违规类型下的样本案件，便于对照阅读")
        rows = []
        for vtype, cases in list(typical.items())[:10]:
            for case in cases:
                rows.append(
                    {
                        "违规类型": vtype,
                        "当事人": case.get("punished_entities", "") or "—",
                        "日期": case.get("date", "") or "—",
                        "处罚": case.get("punishment", "") or "—",
                        "摘要": (case.get("violation_summary") or "")[:120] or "—",
                    }
                )
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                width="stretch",
                hide_index=True,
                column_config={
                    "违规类型": st.column_config.TextColumn("违规类型", width="medium"),
                    "当事人": st.column_config.TextColumn("当事人", width="medium"),
                    "日期": st.column_config.TextColumn("日期", width="small"),
                    "处罚": st.column_config.TextColumn("处罚", width="medium"),
                    "摘要": st.column_config.TextColumn("摘要", width="large"),
                },
            )
