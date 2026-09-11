"""Plotly 图表封装：统一品牌配色、字体、悬停提示与留白。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

__all__ = [
    "PALETTE",
    "style",
    "bar",
    "hbar",
    "donut",
    "trend",
    "grouped_bar",
    "heat_by_month",
]

#: 品牌色板（深蓝 → 天蓝 → 琥珀，附少量区分度高的补充色）
PALETTE: List[str] = [
    "#354e92", "#2563EB", "#0EA5E9", "#F59E0B", "#16A34A",
    "#8B5CF6", "#EF4444", "#0F766E", "#DB2777", "#CA8A04",
    "#475569", "#22D3EE", "#A3E635", "#FB923C", "#6366F1",
]

_FONT = "'Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif"


def style(fig: go.Figure, height: int = 340, legend: bool = True, margin: int = 42) -> go.Figure:
    """统一图表版式：透明背景、品牌字体、紧凑边距与悬停样式。"""
    fig.update_layout(
        height=height,
        margin={"l": margin, "r": 18, "t": 34, "b": margin},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": _FONT, "size": 12.5, "color": "#475569"},
        title={"font": {"size": 15, "color": "#354e92"}, "x": 0.01, "xanchor": "left"},
        showlegend=legend,
        legend={
            "orientation": "h", "yanchor": "bottom", "y": 1.0,
            "xanchor": "right", "x": 1.0, "bgcolor": "rgba(0,0,0,0)",
        },
        hoverlabel={"bgcolor": "#0F172A", "font": {"color": "#F8FAFC", "family": _FONT}},
        transition={"duration": 380, "easing": "cubic-in-out"},
    )
    fig.update_xaxes(gridcolor="#EEF2F9", zerolinecolor="#E2E8F0", linecolor="#E2E8F0")
    fig.update_yaxes(gridcolor="#EEF2F9", zerolinecolor="#E2E8F0", linecolor="#E2E8F0")
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
            x=list(labels), y=list(values), marker_color=color,
            marker_line_width=0, hovertemplate="%{x}<br>%{y} 例<extra></extra>",
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
    """横向条形图（适合较长的违规类型 / 法规名称）。"""
    fig = go.Figure(
        go.Bar(
            y=list(labels), x=list(values), orientation="h",
            marker_color=color, marker_line_width=0,
            hovertemplate="%{y}<br>%{x} 例<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed")
    return _with_title(fig, title, height)


def donut(
    labels: Sequence[str],
    values: Sequence[int],
    title: str = "",
    height: int = 340,
    colors: Optional[Sequence[str]] = None,
) -> go.Figure:
    """环形图（用于构成占比）。"""
    palette = list(colors or PALETTE)
    fig = go.Figure(
        go.Pie(
            labels=list(labels), values=list(values), hole=0.58,
            marker={"colors": palette, "line": {"color": "#FFFFFF", "width": 2}},
            textinfo="label+percent", textposition="outside",
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
    secondary: Optional[Dict[str, Sequence[int]]] = None,
) -> go.Figure:
    """时间趋势（支持叠加机构/人员两条曲线）。"""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(periods), y=list(values), mode="lines+markers", name=name,
            line={"color": "#2563EB", "width": 3, "shape": "spline", "smoothing": 0.4},
            fill="tozeroy", fillcolor="rgba(37,99,235,.10)",
            hovertemplate="%{x}<br>%{y} 例<extra></extra>",
        )
    )
    if secondary:
        for label, series in secondary.items():
            fig.add_trace(
                go.Scatter(
                    x=list(periods), y=list(series), mode="lines+markers", name=label,
                    line={"width": 2, "shape": "spline", "smoothing": 0.4},
                    hovertemplate="%{x}<br>%{y} 例<extra></extra>",
                )
            )
    fig.update_xaxes(type="category")
    return _with_title(fig, title, height)


def grouped_bar(
    categories: Sequence[str],
    series: Dict[str, Sequence[int]],
    title: str = "",
    height: int = 360,
) -> go.Figure:
    """分组柱状图（机构 vs 个人对比）。"""
    frame = pd.DataFrame({"category": list(categories)})
    for name, values in series.items():
        frame[name] = list(values)
    fig = px.bar(
        frame.melt(id_vars="category", var_name="主体类型", value_name="案例数"),
        x="category", y="案例数", color="主体类型", barmode="group",
        color_discrete_map={"机构": "#2563EB", "个人": "#F59E0B"},
    )
    fig.update_traces(hovertemplate="%{x}<br>%{legendgroup}：%{y} 例<extra></extra>")
    return _with_title(fig, title, height)


def heat_by_month(rows: Iterable[Dict[str, Any]], title: str = "", height: int = 320) -> go.Figure:
    """按年 × 月的热力图，展示处分密度的时序分布。"""
    frame = pd.DataFrame([item for item in rows if item.get("date")])
    if frame.empty or "date" not in frame.columns:
        fig = go.Figure()
        fig.add_annotation(text="暂无数据", showarrow=False, font={"size": 16, "color": "#94A3B8"})
        return style(fig, height=height, legend=False)

    frame["date"] = frame["date"].astype(str)
    frame["year"] = frame["date"].str[:4]
    frame["month"] = frame["date"].str[5:7]
    counts = frame.groupby(["year", "month"]).size().reset_index(name="count")
    matrix = counts.pivot(index="year", columns="month", values="count").fillna(0)
    matrix = matrix.reindex(columns=[f"{m:02d}" for m in range(1, 13)])
    fig = go.Figure(
        go.Heatmap(
            z=matrix.values, x=[f"{int(m)}月" for m in matrix.columns], y=list(matrix.index),
            colorscale=[[0, "#F8FAFF"], [0.35, "#BFDBFE"], [0.7, "#2563EB"], [1, "#354e92"]],
            hovertemplate="%{y}年 %{x}<br>%{z} 例<extra></extra>",
            xgap=2, ygap=2,
        )
    )
    return _with_title(fig, title, height, legend=False)


def _with_title(fig: go.Figure, title: str, height: int, legend: bool = True) -> go.Figure:
    styled = style(fig, height=height, legend=legend)
    if title:
        styled.update_layout(title_text=title)
    return styled
