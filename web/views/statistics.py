"""统计分析：违规类型、处罚措施、机构个人对比、法规来源与时间趋势。"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_HERE = Path(__file__).resolve()
for _path in (str(_HERE.parents[2]), str(_HERE.parents[1])):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from components import charts, ui  # noqa: E402
from components.data import load_rows, load_stats  # noqa: E402
from regwatch.storage import DATASETS, DATASET_LABELS  # noqa: E402

ui.apply_theme()

TITLE_HTML = (
    '<div class="rw-title" style="color:#354e92">统计分析</div>'
    '<div style="color:#475569;margin:2px 0 6px">'
    "多维度洞察违规分布、处罚力度与监管趋势</div>"
)
st.markdown(TITLE_HTML, unsafe_allow_html=True)

selection = st.multiselect(
    "统计范围（数据集）", list(DATASETS),
    format_func=DATASET_LABELS.get, default=list(DATASETS),
)
selection = selection or list(DATASETS)
stats = load_stats(selection)
basic = stats.get("basic", {})
total = int(basic.get("total") or 0)

if not total:
    ui.empty_state("所选范围内暂无案例", "请调整数据集范围，或先运行抓取与摘要任务")
    st.stop()

st.caption(
    f"统计口径：{total:,} 例案例（{basic.get('date_range') or '—'}），"
    "违规类型按顿号/分号拆分后归一到分类体系，同一案例可计入多个类型。"
)

tab1, tab2, tab3, tab4 = st.tabs(["违规类型", "处罚措施", "机构与个人", "法规与趋势"])

# ── 页签一：违规类型 ──
with tab1:
    distribution = stats.get("violation_distribution") or []
    if not distribution:
        ui.empty_state("暂无违规类型数据", "请先运行结构化摘要提取")
    else:
        col_a, col_b = st.columns([3, 2], gap="large")
        with col_a:
            top = distribution[:15]
            st.plotly_chart(
                charts.hbar(
                    [item["type"] for item in top][::-1],
                    [item["count"] for item in top][::-1],
                    title="违规类型分布（TOP 15）", height=520,
                ),
                width="stretch",
            )
        with col_b:
            st.markdown("**排名明细**")
            rows = max(1, len(distribution))
            for index, item in enumerate(distribution[:25], 1):
                percent = item["count"] / total * 100
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:3px 0;border-bottom:1px solid #EEF2F9;font-size:13px">'
                    f'<span>{index}. {item["type"]}</span>'
                    f'<span style="color:#2563EB;font-weight:600">{item["count"]} 例'
                    f' <span style="color:#94A3B8;font-weight:400">({percent:.1f}%)</span>'
                    f"</span></div>",
                    unsafe_allow_html=True,
                )
            if rows > 25:
                st.caption(f"其余 {rows - 25} 种类型见报告附件")

        st.markdown("**代表案例**")
        typical = (stats.get("typical") or {}).get(distribution[0]["type"]) or []
        for case in typical[:2]:
            st.markdown(
                '<div class="rw-panel" style="padding:10px 14px">'
                + ui.badges([
                    (str(case.get("entity_display") or ""), "primary"),
                    (str(case.get("date") or ""), "muted"),
                ])
                + f'<div style="font-size:13px;color:#475569;margin-top:6px">'
                + f"{case.get('violation_summary') or '（无摘要）'}</div></div>",
                unsafe_allow_html=True,
            )

# ── 页签二：处罚措施 ──
with tab2:
    categories = stats.get("punishment_categories") or []
    details = stats.get("punishment_details") or []
    col_a, col_b = st.columns(2, gap="large")
    with col_a:
        if categories:
            st.plotly_chart(
                charts.donut(
                    [item["category"] for item in categories[:8]],
                    [item["count"] for item in categories[:8]],
                    title="处罚类别构成", height=360,
                ),
                width="stretch",
            )
        else:
            ui.empty_state("暂无处罚数据")
    with col_b:
        if details:
            st.plotly_chart(
                charts.hbar(
                    [item["punishment"] for item in details[:12]][::-1],
                    [item["count"] for item in details[:12]][::-1],
                    title="具体处罚措施（TOP 12）", height=360, color="#F59E0B",
                ),
                width="stretch",
            )
        else:
            ui.empty_state("暂无处罚明细")

# ── 页签三：机构与个人 ──
with tab3:
    comparison = stats.get("comparison") or {}
    inst = dict(comparison.get("inst_violations") or [])
    pers = dict(comparison.get("pers_violations") or [])
    if not inst and not pers:
        ui.empty_state("暂无对比数据")
    else:
        names = sorted(set(inst) | set(pers),
                       key=lambda name: inst.get(name, 0) + pers.get(name, 0), reverse=True)[:12]
        st.plotly_chart(
            charts.grouped_bar(
                names,
                {"机构": [inst.get(name, 0) for name in names],
                 "个人": [pers.get(name, 0) for name in names]},
                title="机构 vs 个人：违规类型对比（TOP 12）", height=400,
            ),
            width="stretch",
        )
        col_inst, col_pers = st.columns(2, gap="large")
        with col_inst:
            st.markdown("**机构处罚 TOP5**")
            for name, count in (comparison.get("inst_punishments") or {}).most_common():
                st.markdown(f"- {name}（{count} 例）")
        with col_pers:
            st.markdown("**人员处罚 TOP5**")
            for name, count in (comparison.get("pers_punishments") or {}).most_common():
                st.markdown(f"- {name}（{count} 例）")

# ── 页签四：法规与趋势 ──
with tab4:
    col_a, col_b = st.columns([2, 3], gap="large")
    with col_a:
        legal_top = stats.get("legal_basis_top") or []
        if legal_top:
            st.plotly_chart(
                charts.hbar(
                    [item["law"] for item in legal_top[:12]][::-1],
                    [item["count"] for item in legal_top[:12]][::-1],
                    title="法规引用 TOP 12", height=420, color="#0EA5E9",
                ),
                width="stretch",
            )
        else:
            ui.empty_state("暂无法规数据")
    with col_b:
        trend = stats.get("time_trend") or []
        if trend:
            st.plotly_chart(
                charts.trend(
                    [item["period"] for item in trend],
                    [item["count"] for item in trend],
                    title="月度处分趋势",
                    height=240,
                    secondary={
                        "机构": [item.get("institutions", 0) for item in trend],
                        "个人": [item.get("personnel", 0) for item in trend],
                    },
                ),
                width="stretch",
            )
            st.plotly_chart(
                charts.heat_by_month(load_rows(selection), title="年度 × 月处分密度", height=260),
                width="stretch",
            )
        else:
            ui.empty_state("暂无趋势数据")

    bureau_top = stats.get("bureau_top") or []
    if bureau_top:
        st.markdown("**来源局分布（仅 CSRC，TOP 15）**")
        st.plotly_chart(
            charts.bar(
                [item[0] for item in bureau_top],
                [item[1] for item in bureau_top],
                height=280, color="#354e92",
            ),
            width="stretch",
        )
