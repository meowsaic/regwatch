"""总览看板：规模、构成、违规类型 TOP10、来源局与趋势。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import streamlit as st

from regwatch.domain import CaseQuery
from regwatch.web.components import charts, ui
from regwatch.web.components.data import (
    cache_key,
    load_overview,
    load_rows,
    load_totals,
    rows_column_config,
    rows_frame,
)
from regwatch.web.state import build_query

__all__ = ["render"]

_LATEST_LIMIT = 12

#: 趋势两图（年度柱 / 月份热力）的固定起点：库内数据重心在 2022 年之后，
#: 更早年份零星案例只会压扁坐标轴；趋势看全貌，不随「日期」筛选缩放。
_TREND_FLOOR = "2022-01-01"


def _trend_query(query: CaseQuery) -> CaseQuery:
    """构造趋势图专用查询：日期起点固定为 2022 年，忽略会话日期筛选。"""
    return query.replace(date_from=_TREND_FLOOR, date_to=None)


def render() -> None:
    query = build_query()
    overview = load_overview(cache_key(query))

    date_min = str(overview.get("date_min") or "")
    date_max = str(overview.get("date_max") or "")
    range_chip = f"日期 {date_min} ~ {date_max}" if date_min or date_max else ""
    ui.page_header(
        "总览看板",
        "中基协纪律处分与证监会处罚 / 监管措施的整体规模与分布",
        chips=[
            item for item in (f"共 {overview.get('total', 0):,} 例（基金相关）", range_chip) if item
        ],
    )

    if not overview.get("total"):
        ui.empty_state("库中暂无案例", "先到「任务中心」运行一次抓取。")
        return

    # skipped 不在默认查询口径内，单独取全库数展示
    status = overview.get("status", {})
    skipped = int(load_totals().get("csrc.skipped", 0))
    ui.metric_cards(
        [
            {"label": "案例总数（基金相关）", "value": f"{overview['total']:,}"},
            {"label": "已提取摘要", "value": status.get("done", 0), "tone": "ok"},
            {"label": "待提取", "value": status.get("pending", 0), "tone": "amber"},
            {
                "label": "判定非基金相关",
                "value": f"{skipped:,}",
                "tone": "muted",
                "hint": "已排除，不计入统计",
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

    bureau_items = overview.get("bureau") or []
    if bureau_items:
        # HQ / 派出机构英文代码对读者不够友好，尽量展示原始 bureau 字段
        bureau_names = [str(name or "未知") for name, _ in bureau_items[:10]]
        bureau_counts = [int(count) for _, count in bureau_items[:10]]
        st.plotly_chart(
            charts.hbar(bureau_names, bureau_counts, title="来源机构 TOP10（CSRC）", height=360),
            width="stretch",
        )

    # 趋势两图用固定 2022+ 窗口的独立查询（不受会话日期筛选影响）
    trend_overview = load_overview(cache_key(_trend_query(query)))
    trend_rows = trend_overview.get("time_trend", [])
    yearly_rows = trend_overview.get("time_trend_yearly", [])
    if yearly_rows:
        periods = [row["period"] for row in yearly_rows]
        counts = [row["count"] for row in yearly_rows]
        estimated = _estimate_full_year(periods, counts)
        trend_col, heat_col = st.columns([1.2, 1])
        with trend_col:
            st.plotly_chart(
                charts.yearly_bar(
                    periods,
                    counts,
                    estimated=estimated,
                    title="案例数量年度趋势",
                ),
                width="stretch",
            )
        with heat_col:
            if trend_rows:
                st.plotly_chart(
                    charts.heat_from_periods(
                        [row["period"] for row in trend_rows],
                        [row["count"] for row in trend_rows],
                        title="月份密度",
                    ),
                    width="stretch",
                )

    ui.section_header("最新案例", "按处分日期倒序的前 12 条")
    rows = load_rows(cache_key(build_query(limit=_LATEST_LIMIT)))
    if not rows:
        ui.empty_state("暂无案例")
        return
    st.dataframe(
        rows_frame(rows),
        width="stretch",
        hide_index=True,
        column_config=rows_column_config(),
        # 总览只展示摘要列，处罚明细留给案例浏览页
        column_order=["日期", "来源", "标题", "当事人", "违规类型", "状态"],
    )


def _estimate_full_year(periods: Sequence[str], counts: Sequence[int]) -> dict[str, int]:
    """对数据已延伸到今天的当前年度，按已过天数比例推算全年案例数。"""
    today = date.today()
    estimates: dict[str, int] = {}
    for period, count in zip(periods, counts, strict=False):
        try:
            year = int(str(period))
        except ValueError:
            continue
        if year != today.year:
            continue
        elapsed = (today - date(year, 1, 1)).days + 1
        total_days = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        estimates[str(period)] = round(int(count) * total_days / elapsed)
    return estimates


def _dataset_label(value: str) -> str:
    from regwatch.domain import Dataset

    parsed = Dataset.parse(value)
    return parsed.label if parsed else value
