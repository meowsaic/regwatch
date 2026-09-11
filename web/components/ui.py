"""界面基础组件：主题注入、指标卡、徽标、通用控件。

视觉规范：浅色底 + 深蓝主色 + 琥珀点缀的「监管科技数据看板」风格；
卡片化布局、清爽留白、悬停微动效。
"""

from __future__ import annotations

import html
from typing import Any, Dict, Iterable, List, Optional, Sequence

import streamlit as st

__all__ = [
    "apply_theme",
    "page_header",
    "metric_cards",
    "badge",
    "badges",
    "callout",
    "empty_state",
    "status_badge",
    "filter_bar",
    "detail_rows",
]

BRAND = {
    "primary": "#354e92",
    "accent": "#2563EB",
    "sky": "#0EA5E9",
    "amber": "#F59E0B",
    "bg": "#F6F8FC",
    "card": "#FFFFFF",
    "text": "#0F172A",
    "muted": "#475569",
    "faint": "#94A3B8",
    "line": "#E2E8F0",
    "ok": "#16A34A",
    "danger": "#DC2626",
    "warn": "#F59E0B",
}

_TONE_COLORS = {
    "primary": ("#EEF2FF", "#354e92"),
    "accent": ("#E0ECFF", "#2563EB"),
    "amber": ("#FFFBEB", "#B45309"),
    "ok": ("#DCFCE7", "#15803D"),
    "danger": ("#FEE2E2", "#B91C1C"),
    "muted": ("#F1F5F9", "#475569"),
    "sky": ("#E0F2FE", "#0369A1"),
}

# 指标卡数值颜色随 tone 变化，深色数字保证在白卡上清晰可读
_TONE_VALUE_COLORS = {
    "primary": "#354e92",
    "accent": "#1D4ED8",
    "amber": "#B45309",
    "ok": "#15803D",
    "danger": "#B91C1C",
    "muted": "#334155",
    "sky": "#0369A1",
}

_THEME_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap');

:root {{
  --rw-primary:{BRAND['primary']}; --rw-accent:{BRAND['accent']}; --rw-amber:{BRAND['amber']};
  --rw-bg:{BRAND['bg']}; --rw-card:{BRAND['card']}; --rw-text:{BRAND['text']};
  --rw-muted:{BRAND['muted']}; --rw-line:{BRAND['line']};
}}
.stApp {{
  background:
    radial-gradient(1200px 480px at 88% -10%, rgba(37,99,235,.10), transparent 60%),
    radial-gradient(900px 420px at -5% 0%, rgba(245,158,11,.10), transparent 55%),
    {BRAND['bg']};
  color:{BRAND['text']};
  font-family:'Noto Sans SC','PingFang SC','Microsoft YaHei',system-ui,-apple-system,sans-serif;
  font-size:14px;
}}
h1,.rw-title{{font-size:30px;font-weight:700;letter-spacing:.3px}}
h2{{font-size:20px;font-weight:700;margin-bottom:.2rem}}
h3{{font-size:16px;font-weight:600;color:{BRAND['primary']}}}
p,span,div{{font-size:14px}}
/* 顶部留白需大于固定 header 高度，否则首屏内容被遮挡 */
.block-container{{padding-top:4.2rem;padding-bottom:3.2rem;max-width:1400px}}

/* ── 侧边导航 ── */
section[data-testid="stSidebar"] {{
  background:linear-gradient(180deg,#1e2f5e 0%,#2a3f7d 55%,#34508f 100%);
  border-right:none;
}}
section[data-testid="stSidebar"] *{{color:#E8EEFB !important}}
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] span{{font-weight:600}}
section[data-testid="stSidebar"] .stRadio label, section[data-testid="stSidebar"] .stSelectbox label{{font-size:13px}}
section[data-testid="stSidebar"] hr{{border-color:rgba(255,255,255,.14)}}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{{font-size:12.5px;opacity:.78}}
/* 侧边栏按钮：深色底上使用半透明白底 + 浅色文字，避免白字白底看不清 */
section[data-testid="stSidebar"] .stButton > button{{
  background:rgba(255,255,255,.12); color:#E8EEFB !important;
  border:1px solid rgba(255,255,255,.30); border-radius:10px; font-weight:600;
}}
section[data-testid="stSidebar"] .stButton > button:hover{{
  background:rgba(255,255,255,.22); border-color:rgba(255,255,255,.52);
  transform:translateY(-1px); box-shadow:0 6px 16px rgba(0,0,0,.22);
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]{{
  background:linear-gradient(135deg,{BRAND['accent']},{BRAND['sky']});
  border:none; color:#fff !important;
}}

/* ── 指标卡 ── */
.rw-metrics{{display:grid;gap:14px;margin:6px 0 18px}}
.rw-card{{
  position:relative;overflow:hidden;background:{BRAND['card']};border:1px solid {BRAND['line']};
  border-radius:16px;padding:16px 18px 14px;box-shadow:0 2px 10px rgba(15,23,42,.05);
  transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
  animation:rw-rise .35s ease both;
}}
.rw-card:hover{{transform:translateY(-3px);box-shadow:0 12px 26px rgba(37,99,235,.14);border-color:#c7d7f7}}
.rw-card::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:linear-gradient(180deg,{BRAND['accent']},{BRAND['sky']});}}
.rw-card.rw-amber::before{{background:linear-gradient(180deg,{BRAND['amber']},#FBBF24)}}
.rw-card.rw-ok::before{{background:linear-gradient(180deg,{BRAND['ok']},#4ADE80)}}
.rw-card.rw-danger::before{{background:linear-gradient(180deg,{BRAND['danger']},#F87171)}}
.rw-label{{font-size:12.5px;color:{BRAND['muted']};letter-spacing:.4px;font-weight:500}}
.rw-value{{font-size:28px;font-weight:700;color:{BRAND['primary']};line-height:1.25;margin-top:2px}}
.rw-hint{{font-size:12px;color:{BRAND['faint']};margin-top:3px}}
@keyframes rw-rise{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}

/* ── 徽标 / 标签 ── */
.rw-badge{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;
  margin:0 6px 6px 0;white-space:nowrap}}
.rw-chip{{display:inline-block;padding:3px 10px;margin:0 6px 6px 0;border-radius:8px;font-size:12px;
  background:#EEF2FF;color:{BRAND['primary']};border:1px solid #DBE4FB}}
.rw-panel{{background:{BRAND['card']};border:1px solid {BRAND['line']};border-radius:14px;
  padding:14px 18px;margin:8px 0 14px;box-shadow:0 2px 8px rgba(15,23,42,.04)}}
.rw-detail{{width:100%;border-collapse:collapse;margin-top:6px}}
.rw-detail td{{padding:6px 10px;border-bottom:1px solid {BRAND['line']};vertical-align:top;font-size:13.5px}}
.rw-detail td:first-child{{width:112px;color:{BRAND['muted']};white-space:nowrap}}
.rw-empty{{padding:26px;text-align:center;color:{BRAND['faint']};background:#FBFCFE;
  border:1px dashed {BRAND['line']};border-radius:14px}}

/* ── Streamlit 控件微调 ── */
div[data-testid="stMetric"]{{background:{BRAND['card']};border:1px solid {BRAND['line']};
  border-radius:14px;padding:14px 16px;box-shadow:0 2px 8px rgba(15,23,42,.04)}}
div[data-testid="stMetric"] label{{color:{BRAND['muted']} !important;font-size:12.5px !important}}
div[data-testid="stMetricValue"]{{color:{BRAND['primary']} !important;font-weight:700 !important}}
.stButton > button{{border-radius:10px;font-weight:600;border:1px solid #D7E0F2;
  transition:transform .15s ease, box-shadow .15s ease}}
.stButton > button:hover{{transform:translateY(-1px);box-shadow:0 6px 16px rgba(37,99,235,.16)}}
.stButton > button[kind="primary"]{{background:linear-gradient(135deg,{BRAND['accent']},{BRAND['sky']});
  border:none;color:#fff}}
.stDownloadButton > button, .stFormSubmitButton > button{{border-radius:10px}}
div[data-testid="stDataFrame"]{{border:1px solid {BRAND['line']};border-radius:12px;overflow:hidden}}
div[data-testid="stExpander"]{{border:1px solid {BRAND['line']};border-radius:12px;background:{BRAND['card']}}}
div[data-testid="stExpander"] summary{{font-weight:600}}
div[data-baseweb="tab"]{{font-size:14px}}
div[data-baseweb="tab-highlight"]{{background:{BRAND['accent']}}}
.stTabs [data-baseweb="tab-list"]{{gap:6px}}
.stTabs [data-baseweb="tab"]{{padding:6px 14px;border-radius:9px 9px 0 0}}
input,textarea{{border-radius:9px !important}}
.stProgress > div > div{{background:linear-gradient(90deg,{BRAND['accent']},{BRAND['sky']})}}
[data-testid="stStatusWidget"]{{display:none}}
footer{{visibility:hidden}}
</style>
"""


def apply_theme() -> None:
    """注入全局主题样式（幂等，可在每个页面调用）。"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "", chips: Iterable[str] = ()) -> None:
    """页面标题区：大标题 + 说明 + 可选标签。"""
    chips_html = "".join(f'<span class="rw-chip">{html.escape(chip)}</span>' for chip in chips)
    st.markdown(
        f'<div style="margin-bottom:14px">'
        f'<div class="rw-title" style="color:{BRAND["primary"]}">{html.escape(title)}</div>'
        + (f'<div style="color:{BRAND["muted"]};margin-top:2px">{html.escape(subtitle)}</div>' if subtitle else "")
        + (f'<div style="margin-top:8px">{chips_html}</div>' if chips_html else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def metric_cards(
    items: Sequence[Dict[str, Any]],
    columns: Optional[int] = None,
) -> None:
    """渲染一排指标卡。

    每个 item 支持：``label`` / ``value`` / ``hint`` / ``tone``（primary|amber|ok|danger）。
    """
    if not items:
        return
    cols = columns or min(len(items), 4)
    cards: List[str] = []
    for item in items:
        tone = str(item.get("tone", "primary"))
        value_color = _TONE_VALUE_COLORS.get(tone, BRAND["primary"])
        cards.append(
            f'<div class="rw-card{" rw-" + tone if tone and tone != "primary" else ""}">'
            f'<div class="rw-label">{html.escape(str(item.get("label", "")))}</div>'
            f'<div class="rw-value" style="color:{value_color}">'
            f'{html.escape(str(item.get("value", "")))}</div>'
            f'<div class="rw-hint">{html.escape(str(item.get("hint", "")))}</div>'
            "</div>"
        )
    st.markdown(
        f'<div class="rw-metrics" style="grid-template-columns:repeat({cols},minmax(0,1fr))">'
        + "".join(cards)
        + "</div>",
        unsafe_allow_html=True,
    )


def badge(text: str, tone: str = "primary") -> str:
    """返回一枚徽标的 HTML（需配合 ``unsafe_allow_html=True``）。"""
    bg, color = _TONE_COLORS.get(tone, _TONE_COLORS["primary"])
    return f'<span class="rw-badge" style="background:{bg};color:{color}">{html.escape(text)}</span>'


def badges(pairs: Iterable[tuple[str, str]]) -> str:
    """批量渲染徽标：``[(文本, tone), ...]``。"""
    return "".join(badge(text, tone) for text, tone in pairs)


def status_badge(status: str) -> str:
    """任务/案例状态徽标。"""
    mapping = {
        "done": ("已完成", "ok"),
        "success": ("已完成", "ok"),
        "running": ("执行中", "accent"),
        "pending": ("待处理", "muted"),
        "failed": ("失败", "danger"),
        "skipped": ("已跳过", "amber"),
        "cancelled": ("已取消", "muted"),
    }
    label, tone = mapping.get(status, (status, "muted"))
    return badge(label, tone)


def callout(text: str, tone: str = "accent", icon: str = "ℹ") -> None:
    """轻量提示条。"""
    _, color = _TONE_COLORS.get(tone, _TONE_COLORS["accent"])
    st.markdown(
        f'<div class="rw-panel" style="border-left:4px solid {color}">'
        f'<span style="margin-right:8px">{icon}</span>{html.escape(text)}</div>',
        unsafe_allow_html=True,
    )


def empty_state(text: str, hint: str = "") -> None:
    """空数据占位。"""
    st.markdown(
        f'<div class="rw-empty"><div style="font-size:15px;font-weight:600;margin-bottom:4px">'
        f'{html.escape(text)}</div><div style="font-size:12.5px">{html.escape(hint)}</div></div>',
        unsafe_allow_html=True,
    )


def detail_rows(pairs: Sequence[tuple[str, Any]]) -> str:
    """键值对详情表 HTML。"""
    rows = "".join(
        f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value or '—'))}</td></tr>"
        for key, value in pairs
    )
    return f'<table class="rw-detail">{rows}</table>'


def filter_bar(**defaults: Any) -> Dict[str, Any]:
    """统一筛选条样式占位：保持各页筛选控件间距一致。"""
    st.markdown('<div class="rw-panel">', unsafe_allow_html=True)
    return defaults
