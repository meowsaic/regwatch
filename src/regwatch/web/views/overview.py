"""总览看板：规模、构成、违规类型 TOP10 与趋势。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from regwatch.web.components import charts, ui
from regwatch.web.components.data import cache_key, load_overview, load_rows
from regwatch.web.state import build_query

__all__ = ["render"]

_LATEST_LIMIT = 12


def render() -> None:
    query = build_query()
    overview = load_overview(cache_key(query))

    ui.page_header(
        "总览看板",
        "中基协纪律处分与证监会处罚 / 监管措施的整体规模与分布",
        chips=[f"数据版本 {overview.get('total', 0)} 例"],
    )

    if not overview.get("total"):
        ui.empty_state("库中暂无案例", "先到「任务中心」运行一次抓取或导入（db import）。")
        return

    status = overview.get("status", {})
    ui.metric_cards(
        [
            {"label": "案例总数", "value": f"{overview['total']:,}"},
            {"label": "已提取摘要", "value": status.get("done", 0), "tone": "ok"},
            {"label": "待提取", "value": status.get("pending", 0), "tone": "amber"},
            {
                "label": "判定非基金相关",
                "value": status.get("skipped", 0),
                "tone": "muted",
                "hint": "仅 CSRC",
            },
        ]
    )

    left, right = st.columns([1, 1.2])
    with left:
        dataset_items = overview.get("dataset", {})
        labels = [_dataset_label(key) for key in dataset_items]
        st.plotly_chart(
            charts.donut(labels, list(dataset_items.values()), title="数据集构成"),
            width="stretch",
        )
    with right:
        violations = overview.get("violations", [])[:10]
        if violations:
            names = [name for name, _ in violations]
            counts = [count for _, count in violations]
            st.plotly_chart(
                charts.hbar(names, counts, title="违规类型 TOP10"),
                width="stretch",
            )
        else:
            ui.empty_state("暂无违规类型数据", "运行一次结构化摘要提取后即可看到分布。")

    trend_rows = overview.get("time_trend", [])
    if trend_rows:
        st.plotly_chart(
            charts.trend(
                [row["period"] for row in trend_rows],
                [row["count"] for row in trend_rows],
                title="案例数量月度趋势",
                secondary={
                    "机构": [row["institutions"] for row in trend_rows],
                    "个人": [row["personnel"] for row in trend_rows],
                },
            ),
            width="stretch",
        )

    st.markdown("#### 最新案例")
    rows = load_rows(cache_key(build_query(limit=_LATEST_LIMIT)))
    if not rows:
        ui.empty_state("暂无案例")
        return
    frame = pd.DataFrame(
        [
            {
                "日期": row["date"],
                "来源": row["dataset_label"],
                "标题": row["title"],
                "当事人": row["punished_entities"],
                "违规类型": row["violation_type"],
                "状态": row["status_label"],
            }
            for row in rows
        ]
    )
    st.dataframe(frame, width="stretch", hide_index=True)


def _dataset_label(value: str) -> str:
    from regwatch.domain import Dataset

    parsed = Dataset.parse(value)
    return parsed.label if parsed else value
