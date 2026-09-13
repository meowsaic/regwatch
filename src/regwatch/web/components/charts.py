"""Plotly 图表封装：统一品牌配色、字体、悬停提示与留白。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

__all__ = [
    "PALETTE",
    "bar",
    "donut",
    "grouped_bar",
    "hbar",
    "heat_from_periods",
    "style",
    "trend",
    "yearly_bar",
]

#: 品牌色板（深蓝 → 天蓝 → 琥珀，附少量区分度高的补充色）
PALETTE: list[str] = [
    "#354e92",
    "#2563EB",
    "#0EA5E9",
    "#F59E0B",
    "#16A34A",
    "#8B5CF6",
    "#EF4444",
    "#0F766E",
    "#DB2777",
    "#CA8A04",
    "#475569",
    "#22D3EE",
    "#A3E635",
    "#FB923C",
    "#6366F1",
]

_FONT = "'Segoe UI','PingFang SC','Microsoft YaHei','Noto Sans SC',system-ui,sans-serif"


def style(
    fig: go.Figure,
    height: int = 340,
    legend: bool = True,
    margin: int = 42,
    title: str = "",
) -> go.Figure:
    """统一图表版式：透明背景、品牌字体、紧凑边距与悬停样式。

    ``title`` 为空时**不写入** title 布局对象——空的 title 对象会被
    Plotly 渲染成字面量 "undefined"。
    """
    layout: dict[str, Any] = {
        "height": height,
        "margin": {"l": margin, "r": 18, "t": 34, "b": margin},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": _FONT, "size": 12.5, "color": "#475569"},
        "showlegend": legend,
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.0,
            "xanchor": "right",
            "x": 1.0,
            "bgcolor": "rgba(0,0,0,0)",
        },
        "hoverlabel": {"bgcolor": "#0F172A", "font": {"color": "#F8FAFC", "family": _FONT}},
        "transition": {"duration": 380, "easing": "cubic-in-out"},
    }
    if title:
        layout["title"] = {
            "text": str(title),
            "font": {"size": 15, "color": "#354e92"},
            "x": 0.01,
            "xanchor": "left",
        }
    fig.update_layout(layout)
    fig.update_xaxes(
        gridcolor="#EEF2F9",
        zerolinecolor="#E2E8F0",
        linecolor="#E2E8F0",
        automargin="bottom",
    )
    fig.update_yaxes(
        gridcolor="#EEF2F9",
        zerolinecolor="#E2E8F0",
        linecolor="#E2E8F0",
        automargin="left",
    )
    return fig


def bar(
    labels: Sequence[str],
    values: Sequence[int],
    title: str = "",
    height: int = 340,
    color: str = "#2563EB",
) -> go.Figure:
    """柱状图。"""
    fig = go.Figure(
        go.Bar(
            x=list(labels),
            y=list(values),
            marker_color=color,
            name="案例数",
            marker_line_width=0,
            hovertemplate="%{x}<br>%{y} 例<extra></extra>",
        )
    )
    return _with_title(fig, title, height, legend=False)


def hbar(
    labels: Sequence[str],
    values: Sequence[int],
    title: str = "",
    height: int = 420,
    color: str = "#2563EB",
) -> go.Figure:
    """横向条形图（适合较长的违规类型 / 法规名称）。

    ``labels`` / ``values`` 按**降序**传入（首项最大），首项将渲染在最上方。
    """
    fig = go.Figure(
        go.Bar(
            y=list(labels),
            x=list(values),
            orientation="h",
            marker_color=color,
            marker_line_width=0,
            name="案例数",
            hovertemplate="%{y}<br>%{x} 例<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed")
    return _with_title(fig, title, height, legend=False)


def donut(
    labels: Sequence[str],
    values: Sequence[int],
    title: str = "",
    height: int = 340,
    colors: Sequence[str] | None = None,
) -> go.Figure:
    """环形图（用于构成占比）。"""
    palette = list(colors or PALETTE)
    fig = go.Figure(
        go.Pie(
            labels=list(labels),
            values=list(values),
            hole=0.58,
            marker={"colors": palette, "line": {"color": "#FFFFFF", "width": 2}},
            textinfo="percent",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont={"size": 12.5, "color": "#FFFFFF"},
            hovertemplate="%{label}<br>%{value} 例（%{percent}）<extra></extra>",
        )
    )
    return _with_title(fig, title, height)


def trend(
    periods: Sequence[str],
    values: Sequence[int],
    title: str = "",
    height: int = 320,
    name: str = "案例数",
    secondary: dict[str, Sequence[int]] | None = None,
) -> go.Figure:
    """时间趋势（支持叠加机构/人员两条曲线）。"""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(periods),
            y=list(values),
            mode="lines+markers",
            name=name,
            line={"color": "#2563EB", "width": 3, "shape": "spline", "smoothing": 0.4},
            fill="tozeroy",
            fillcolor="rgba(37,99,235,.10)",
            hovertemplate="%{x}<br>%{y} 例<extra></extra>",
        )
    )
    if secondary:
        for label, series in secondary.items():
            fig.add_trace(
                go.Scatter(
                    x=list(periods),
                    y=list(series),
                    mode="lines+markers",
                    name=label,
                    line={"width": 2, "shape": "spline", "smoothing": 0.4},
                    hovertemplate="%{x}<br>%{y} 例<extra></extra>",
                )
            )
    fig.update_xaxes(type="category")
    return _with_title(fig, title, height, legend=bool(secondary))


def yearly_bar(
    periods: Sequence[str],
    values: Sequence[int],
    estimated: dict[str, int] | None = None,
    title: str = "",
    height: int = 340,
) -> go.Figure:
    """年度柱状图（机构与个人合并的整体口径）。

    ``estimated`` 为 ``{年份: 推算全年总数}``；不完整年份会在实际值之上
    堆叠一段半透明的「推算」柱，与实际值明确区分。
    """
    est = {str(key): int(value) for key, value in (estimated or {}).items()}
    actual = [int(value) for value in values]
    pairs = list(zip(periods, actual, strict=True))
    projected = [max(0, est.get(str(period), 0) - base) for period, base in pairs]
    full_year = [est.get(str(period), 0) or base for period, base in pairs]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=list(periods),
            y=actual,
            name="实际",
            marker_color="#2563EB",
            marker_line_width=0,
            customdata=full_year,
            hovertemplate="%{x} 年<br>实际 %{y} 例<extra></extra>",
        )
    )
    has_projection = any(projected)
    if has_projection:
        fig.add_trace(
            go.Bar(
                x=list(periods),
                y=projected,
                name="推算（按当前进度折算全年）",
                marker_color="rgba(37,99,235,.32)",
                marker_line_width=0,
                customdata=full_year,
                hovertemplate="%{x} 年推算全年约 %{customdata} 例<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack" if has_projection else "relative")
    fig.update_xaxes(type="category")
    return _with_title(fig, title, height, legend=has_projection)


def grouped_bar(
    categories: Sequence[str],
    series: dict[str, Sequence[int]],
    title: str = "",
    height: int = 360,
) -> go.Figure:
    """分组柱状图（机构 vs 个人对比）。"""
    frame = pd.DataFrame({"category": list(categories)})
    for name, values in series.items():
        frame[name] = list(values)
    fig = px.bar(
        frame.melt(id_vars="category", var_name="主体类型", value_name="案例数"),
        x="category",
        y="案例数",
        color="主体类型",
        barmode="group",
        color_discrete_map={"机构": "#2563EB", "个人": "#F59E0B"},
    )
    fig.update_traces(hovertemplate="%{x}<br>%{legendgroup}：%{y} 例<extra></extra>")
    return _with_title(fig, title, height)


def heat_from_periods(
    periods: Sequence[str],
    counts: Sequence[int],
    title: str = "",
    height: int = 320,
) -> go.Figure:
    """把 ``YYYY-MM`` 聚合序列渲染成年 × 月热力图。

    总览的 ``time_trend`` 已按月聚合，无需再扫原始日期。
    """
    matrix: dict[str, dict[str, int]] = {}
    years: set[str] = set()
    for period, count in zip(periods, counts, strict=False):
        text = str(period or "")
        if len(text) < 7:
            continue
        year, month = text[:4], text[5:7]
        matrix.setdefault(year, {})[month] = int(count)
        years.add(year)
    if not years:
        fig = go.Figure()
        fig.add_annotation(text="暂无数据", showarrow=False, font={"size": 16, "color": "#94A3B8"})
        return style(fig, height=height, legend=False, title=title)

    year_labels = sorted(years)
    month_labels = [f"{m:02d}" for m in range(1, 13)]
    z = [[matrix.get(year, {}).get(month, 0) for month in month_labels] for year in year_labels]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[f"{int(m)}月" for m in month_labels],
            y=year_labels,
            colorscale=[[0, "#F8FAFF"], [0.35, "#BFDBFE"], [0.7, "#2563EB"], [1, "#354e92"]],
            hovertemplate="%{y}年 %{x}<br>%{z} 例<extra></extra>",
            xgap=2,
            ygap=2,
            colorbar={"thickness": 10, "len": 0.6},
        )
    )
    # 年份是分类（纯数字字符串会被 plotly 当数值轴，渲染出 2025.8 这类刻度）
    fig.update_yaxes(type="category")
    return _with_title(fig, title, height, legend=False)


def _with_title(fig: go.Figure, title: str, height: int, legend: bool = True) -> go.Figure:
    return style(fig, height=height, legend=legend, title=title)
