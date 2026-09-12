"""统计分析：违规分布、处罚构成、机构与个人对比、法规与趋势。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from regwatch.web.components import charts, ui
from regwatch.web.components.data import cache_key, load_stats
from regwatch.web.state import build_query, get, set_value

__all__ = ["render"]


def render() -> None:
    ui.page_header("统计分析", "违规类型、处罚措施、主体对比与法规引用")

    with st.expander("统计范围", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            set_value(
                "date_from", st.text_input("起始日期", value=get("date_from", ""), key="stats_from")
            )
        with col2:
            set_value(
                "date_to", st.text_input("结束日期", value=get("date_to", ""), key="stats_to")
            )

    stats = load_stats(cache_key(build_query()))
    total = stats.get("basic", {}).get("total", 0)
    if not total:
        ui.empty_state("暂无可统计的案例", "先运行抓取与摘要提取。")
        return

    st.caption(f"统计口径：共 {total:,} 例；同一案例可计入多个违规类型。")

    tab1, tab2, tab3, tab4 = st.tabs(["违规类型", "处罚措施", "机构与个人", "法规与趋势"])

    with tab1:
        items = stats.get("violation_distribution", [])
        if not items:
            ui.empty_state("暂无违规类型数据")
        else:
            st.plotly_chart(
                charts.hbar(
                    [item["type"] for item in items[:15]],
                    [item["count"] for item in items[:15]],
                    title="违规类型分布 TOP15",
                ),
                width="stretch",
            )
            st.dataframe(pd.DataFrame(items), width="stretch", hide_index=True)

    with tab2:
        categories = stats.get("punishment_categories", [])
        if categories:
            st.plotly_chart(
                charts.donut(
                    [item["category"] for item in categories],
                    [item["count"] for item in categories],
                    title="处罚类别构成",
                ),
                width="stretch",
            )
        details = stats.get("punishment_details", [])
        if details:
            st.dataframe(pd.DataFrame(details), width="stretch", hide_index=True)

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
            ui.empty_state("暂无主体对比数据")

    with tab4:
        laws = stats.get("legal_basis_top", [])
        if laws:
            st.plotly_chart(
                charts.hbar(
                    [item["law"] for item in laws[:15]],
                    [item["count"] for item in laws[:15]],
                    title="法规引用 TOP15",
                ),
                width="stretch",
            )
        trend_rows = stats.get("time_trend", [])
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
        st.markdown("#### 代表案例")
        rows = []
        for vtype, cases in list(typical.items())[:10]:
            for case in cases:
                rows.append(
                    {
                        "违规类型": vtype,
                        "当事人": case.get("punished_entities", ""),
                        "日期": case.get("date", ""),
                        "处罚": case.get("punishment", ""),
                        "摘要": (case.get("violation_summary") or "")[:120],
                    }
                )
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
