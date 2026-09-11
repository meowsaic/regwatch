"""统计分析：违规类型、处罚措施、机构个人对比、法规来源与时间趋势。

支持按日期区间统计：快捷区间（全部 / 近1年 / 近3年 / 近5年）+ 自定义起止日期。
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

from regwatch.storage import DATASET_LABELS, DATASETS
from regwatch.web.components import charts, ui
from regwatch.web.components.data import filter_case_dicts, load_rows, load_stats

ui.apply_theme()

TITLE_HTML = (
    '<div class="rw-title" style="color:#354e92">统计分析</div>'
    '<div style="color:#475569;margin:2px 0 6px">'
    "多维度洞察违规分布、处罚力度与监管趋势</div>"
)
st.markdown(TITLE_HTML, unsafe_allow_html=True)

selection = st.multiselect(
    "统计范围（数据集）",
    list(DATASETS),
    format_func=DATASET_LABELS.get,
    default=list(DATASETS),
)
selection = selection or list(DATASETS)

rows_meta = load_rows(selection)


def _parse_date(raw: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def _shift_years(value: dt.date, years: int) -> dt.date:
    """往前/后推 N 年；2 月 29 日自动回落到 2 月 28 日。"""
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


dated = [d for d in (_parse_date(row.get("date")) for row in rows_meta) if d]
if not dated:
    ui.empty_state("所选数据集暂无带日期的案例", "请调整数据集范围，或先运行抓取与摘要任务")
    st.stop()
min_date, max_date = min(dated), max(dated)

QUICK_RANGES = ("全部", "近1年", "近3年", "近5年")


def _apply_quick_range() -> None:
    """快捷区间变化时同步日期选择器（回调先于控件实例化执行，可安全写入）。"""
    span = st.session_state.get("stat_quick")
    if span == "全部":
        st.session_state["stat_range"] = (min_date, max_date)
    elif span:
        years = int(str(span).removeprefix("近").removesuffix("年") or 1)
        st.session_state["stat_range"] = (_shift_years(max_date, -years), max_date)


quick_col, range_col = st.columns([2, 3], gap="large")
with quick_col:
    st.pills(
        "快捷区间",
        list(QUICK_RANGES),
        key="stat_quick",
        default="全部",
        on_change=_apply_quick_range,
    )
with range_col:
    # 切换数据集后，把会话中残留的区间钳制到新范围内，避免越界
    prev_range = st.session_state.get("stat_range")
    if isinstance(prev_range, (tuple, list)) and prev_range:
        p_start = min(max(prev_range[0], min_date), max_date)
        p_end = min(max(prev_range[-1], min_date), max_date)
        st.session_state["stat_range"] = (min(p_start, p_end), max(p_start, p_end))
    elif isinstance(prev_range, dt.date):
        st.session_state["stat_range"] = min(max(prev_range, min_date), max_date)
    range_value = st.date_input(
        "自定义区间",
        key="stat_range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

if isinstance(range_value, (tuple, list)) and range_value:
    start_date = range_value[0]
    end_date = range_value[-1] if len(range_value) > 1 else range_value[0]
else:
    start_date = end_date = range_value
date_from = start_date.isoformat()
date_to = end_date.isoformat()

stats = load_stats(selection, date_from, date_to)
basic = stats.get("basic", {})
total = int(basic.get("total") or 0)
full_total = len(rows_meta)

if not total:
    ui.empty_state("所选范围与区间内暂无案例", "请调整数据集或统计区间，或先运行抓取与摘要任务")
    st.stop()

scope = f"{total:,} 例案例（{date_from} ~ {date_to}）"
if total != full_total:
    scope += f"，已按区间筛选自全量 {full_total:,} 例"
st.caption(f"统计口径：{scope}；违规类型按顿号/分号拆分后归一到分类体系，同一案例可计入多个类型。")

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
                    [item["type"] for item in top],
                    [item["count"] for item in top],
                    title="违规类型分布（TOP 15）",
                    height=520,
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
                    f"<span>{index}. {item['type']}</span>"
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
                + ui.badges(
                    [
                        (str(case.get("entity_display") or ""), "primary"),
                        (str(case.get("date") or ""), "muted"),
                    ]
                )
                + '<div style="font-size:13px;color:#475569;margin-top:6px">'
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
                    title="处罚类别构成",
                    height=360,
                ),
                width="stretch",
            )
        else:
            ui.empty_state("暂无处罚数据")
    with col_b:
        if details:
            st.plotly_chart(
                charts.hbar(
                    [item["punishment"] for item in details[:12]],
                    [item["count"] for item in details[:12]],
                    title="具体处罚措施（TOP 12）",
                    height=360,
                    color="#F59E0B",
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
        names = sorted(
            set(inst) | set(pers),
            key=lambda name: inst.get(name, 0) + pers.get(name, 0),
            reverse=True,
        )[:12]
        st.plotly_chart(
            charts.grouped_bar(
                names,
                {
                    "机构": [inst.get(name, 0) for name in names],
                    "个人": [pers.get(name, 0) for name in names],
                },
                title="机构 vs 个人：违规类型对比（TOP 12）",
                height=400,
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
                    [item["law"] for item in legal_top[:12]],
                    [item["count"] for item in legal_top[:12]],
                    title="法规引用 TOP 12",
                    height=420,
                    color="#0EA5E9",
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
                charts.heat_by_month(
                    filter_case_dicts(rows_meta, date_from=date_from, date_to=date_to),
                    title="年度 × 月处分密度",
                    height=260,
                ),
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
                height=280,
                color="#354e92",
            ),
            width="stretch",
        )
