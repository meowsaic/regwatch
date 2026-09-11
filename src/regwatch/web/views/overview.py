"""总览看板：核心指标、数据集构成、违规概览与最新案例。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from regwatch.web.components import charts, ui
from regwatch.web.components.data import load_rows, load_stats

ui.apply_theme()

stats = load_stats()
basic = stats.get("basic", {})
rows = load_rows()

ui.page_header(
    "总览看板",
    f"数据区间 {basic.get('date_range') or '—'}",
    chips=[f"{item['label']} {item['count']:,} 例" for item in stats.get("dataset_split", [])],
)

violation_count = len(stats.get("violation_distribution") or [])
mentions = sum(i["count"] for i in stats.get("violation_distribution") or [])
punish_top = (stats.get("punishment_categories") or [{}])[0]
skipped = basic.get("status_counts", {}).get("skipped", 0)

ui.metric_cards(
    [
        {
            "label": "处分案例总数",
            "value": f"{basic.get('total', 0):,}",
            "hint": f"日期范围 {basic.get('date_range') or '—'}",
        },
        {
            "label": "受处分机构",
            "value": f"{basic.get('institution_count', 0):,}",
            "hint": f"占比 {basic.get('institution_count', 0) / max(basic.get('total', 1), 1) * 100:.1f}%",
        },
        {
            "label": "受处分人员",
            "value": f"{basic.get('personnel_count', 0):,}",
            "hint": f"占比 {basic.get('personnel_count', 0) / max(basic.get('total', 1), 1) * 100:.1f}%",
        },
        {
            "label": "违规类型",
            "value": f"{violation_count} 种",
            "hint": f"共 {mentions:,} 次提及",
            "tone": "accent",
        },
        {
            "label": "最高频处罚类别",
            "value": str(punish_top.get("category", "—")),
            "hint": f"{punish_top.get('count', 0)} 例",
            "tone": "amber",
        },
        {
            "label": "判定非基金相关",
            "value": f"{skipped:,}",
            "hint": "由模型精判并跳过",
            "tone": "ok",
        },
    ],
    columns=6,
)

left, right = st.columns([3, 2], gap="large")

with left:
    st.markdown("#### 违规类型 TOP 10")
    distribution = stats.get("violation_distribution") or []
    if distribution:
        top = distribution[:10]
        st.plotly_chart(
            charts.hbar(
                [item["type"] for item in top],
                [item["count"] for item in top],
                height=400,
            ),
            width="stretch",
        )
    else:
        ui.empty_state("暂无违规类型数据", "请先在「任务中心」运行结构化摘要提取")

with right:
    st.markdown("#### 数据集构成")
    split = stats.get("dataset_split") or []
    if split:
        st.plotly_chart(
            charts.donut(
                [item["label"] for item in split],
                [item["count"] for item in split],
                height=270,
            ),
            width="stretch",
        )
    else:
        ui.empty_state("暂无数据")

    st.markdown("#### 年度处分趋势")
    yearly = [item for item in (stats.get("time_trend_yearly") or []) if item["period"] >= "2022"]
    if yearly:
        st.plotly_chart(
            charts.trend(
                [item["period"] for item in yearly],
                [item["count"] for item in yearly],
                height=220,
                name="年度案例数",
            ),
            width="stretch",
        )
        st.caption(
            "统计口径：2022 年起 AMAC 与 CSRC 双数据源覆盖完整，早年仅有 AMAC 自律处分数据。"
        )
    else:
        ui.empty_state("暂无趋势数据")

# ── 最新案例 ──
st.markdown("#### 最新处分案例")
columns = ["date", "dataset_label", "entity_display", "entity_type", "violation_type", "punishment"]
frame = pd.DataFrame(rows[:12])
if frame.empty:
    frame = pd.DataFrame(columns=columns)
frame = frame.reindex(columns=columns).rename(
    columns={
        "date": "日期",
        "dataset_label": "数据集",
        "entity_display": "受处分主体",
        "entity_type": "主体类型",
        "violation_type": "违规类型",
        "punishment": "处罚措施",
    }
)
if frame.empty:
    ui.empty_state("暂无案例", "请在「任务中心」运行抓取任务")
else:
    st.dataframe(frame, width="stretch", hide_index=True, height=320)

st.caption(
    "说明：AMAC 数据存在个别案例的 date/title 与正文落款不一致的情况，深度分析请以案例原文为准。"
)
