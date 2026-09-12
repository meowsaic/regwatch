"""界面基础组件：主题注入、指标卡、徽标、通用控件。

视觉规范：浅色底 + 深蓝主色 + 琥珀点缀的「监管科技数据看板」风格；
卡片化布局、清爽留白、悬停微动效。字号与控件边框按可读性优先调校。
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from typing import Any

import streamlit as st

__all__ = [
    "apply_theme",
    "badge",
    "badges",
    "callout",
    "csv_bytes",
    "date_input_field",
    "detail_rows",
    "download_button",
    "empty_state",
    "field",
    "filter_summary",
    "metric_cards",
    "page_header",
    "parse_date_arg",
    "safe_url",
    "section_header",
    "source_link",
    "status_badge",
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
    "faint": "#64748B",
    "line": "#D8E0EE",
    "ok": "#16A34A",
    "danger": "#DC2626",
    "warn": "#F59E0B",
}

_EMPTY_DISPLAY = "—"

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
:root {{
  --rw-primary:{BRAND["primary"]}; --rw-accent:{BRAND["accent"]}; --rw-amber:{BRAND["amber"]};
  --rw-bg:{BRAND["bg"]}; --rw-card:{BRAND["card"]}; --rw-text:{BRAND["text"]};
  --rw-muted:{BRAND["muted"]}; --rw-line:{BRAND["line"]};
  --rw-sidebar-width:252px;
}}
/* 系统字体栈：离线/内网也能清晰显示中文 */
html, body, .stApp {{
  font-family:"Segoe UI","PingFang SC","Microsoft YaHei","Noto Sans SC",
    system-ui,-apple-system,sans-serif;
}}
.stApp {{
  background:
    radial-gradient(1200px 480px at 88% -10%, rgba(37,99,235,.10), transparent 60%),
    radial-gradient(900px 420px at -5% 0%, rgba(245,158,11,.10), transparent 55%),
    {BRAND["bg"]};
  color:{BRAND["text"]};
  font-size:15px;
  line-height:1.55;
}}
h1,.rw-title{{font-size:1.85rem;font-weight:700;letter-spacing:.2px;line-height:1.25}}
h2{{font-size:1.2rem;font-weight:700;margin-bottom:.25rem}}
h3{{font-size:1.05rem;font-weight:600;color:{BRAND["primary"]}}}
p,span,div,li{{font-size:15px}}
.stMarkdown {{color:{BRAND["text"]}}}
/* 顶部留白需大于固定 header 高度，否则首屏内容被遮挡 */
.block-container{{padding-top:4.0rem;padding-bottom:3rem;max-width:1400px}}

/* ── 侧边导航：收窄 + 深色底上保证对比度 ── */
section[data-testid="stSidebar"], [data-testid="stSidebar"] {{
  min-width:var(--rw-sidebar-width) !important;
  max-width:var(--rw-sidebar-width) !important;
  width:var(--rw-sidebar-width) !important;
  background:linear-gradient(180deg,#1e2f5e 0%,#2a3f7d 55%,#34508f 100%);
  border-right:none;
}}
section[data-testid="stSidebar"] *, [data-testid="stSidebar"] *{{color:#E8EEFB !important}}
section[data-testid="stSidebar"] [data-testid="stSidebarNav"] span{{font-weight:600;font-size:14.5px}}
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stSelectbox label{{font-size:13px}}
section[data-testid="stSidebar"] hr{{border-color:rgba(255,255,255,.14)}}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{{
  font-size:13px;opacity:.92;color:#E8EEFB !important
}}
/* 侧边栏若仍出现 metric（兼容旧布局）：半透明底，避免白底白字 */
section[data-testid="stSidebar"] div[data-testid="stMetric"]{{
  background:rgba(255,255,255,.10) !important;
  border:1px solid rgba(255,255,255,.18) !important;
  box-shadow:none !important;
}}
section[data-testid="stSidebar"] div[data-testid="stMetric"] label,
section[data-testid="stSidebar"] div[data-testid="stMetricValue"],
section[data-testid="stSidebar"] div[data-testid="stMetricDelta"]{{
  color:#E8EEFB !important;
}}
section[data-testid="stSidebar"] .stButton > button{{
  background:rgba(255,255,255,.12); color:#E8EEFB !important;
  border:1px solid rgba(255,255,255,.30); border-radius:10px; font-weight:600;
}}
section[data-testid="stSidebar"] .stButton > button:hover{{
  background:rgba(255,255,255,.22); border-color:rgba(255,255,255,.52);
  transform:translateY(-1px); box-shadow:0 6px 16px rgba(0,0,0,.22);
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]{{
  background:linear-gradient(135deg,{BRAND["accent"]},{BRAND["sky"]});
  border:none; color:#fff !important;
}}
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea {{
  background:rgba(255,255,255,.12) !important;
  color:#0F172A !important;
  border-color:rgba(255,255,255,.35) !important;
}}

/* ── 指标卡 ── */
.rw-metrics{{display:grid;gap:14px;margin:8px 0 18px}}
.rw-card{{
  position:relative;overflow:hidden;background:{BRAND["card"]};border:1px solid {BRAND["line"]};
  border-radius:14px;padding:16px 18px 14px;box-shadow:0 2px 10px rgba(15,23,42,.05);
  transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
  animation:rw-rise .35s ease both;
}}
.rw-card:hover{{transform:translateY(-2px);box-shadow:0 10px 22px rgba(37,99,235,.12);border-color:#c7d7f7}}
.rw-card::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:linear-gradient(180deg,{BRAND["accent"]},{BRAND["sky"]});}}
.rw-card.rw-amber::before{{background:linear-gradient(180deg,{BRAND["amber"]},#FBBF24)}}
.rw-card.rw-ok::before{{background:linear-gradient(180deg,{BRAND["ok"]},#4ADE80)}}
.rw-card.rw-danger::before{{background:linear-gradient(180deg,{BRAND["danger"]},#F87171)}}
.rw-label{{font-size:13px;color:{BRAND["muted"]};letter-spacing:.3px;font-weight:600}}
.rw-value{{font-size:1.75rem;font-weight:700;color:{BRAND["primary"]};line-height:1.2;margin-top:4px}}
.rw-hint{{font-size:12.5px;color:{BRAND["faint"]};margin-top:4px}}
@keyframes rw-rise{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}

/* ── 徽标 / 标签 / 面板 ── */
.rw-badge{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:600;
  margin:0 6px 6px 0;white-space:nowrap}}
.rw-chip{{display:inline-block;padding:3px 10px;margin:0 6px 6px 0;border-radius:8px;font-size:12.5px;
  background:#EEF2FF;color:{BRAND["primary"]};border:1px solid #DBE4FB}}
.rw-panel{{background:{BRAND["card"]};border:1px solid {BRAND["line"]};border-radius:12px;
  padding:14px 16px;margin:8px 0 14px;box-shadow:0 2px 8px rgba(15,23,42,.04)}}
.rw-detail{{width:100%;border-collapse:collapse;margin-top:6px}}
.rw-detail td{{padding:8px 10px;border-bottom:1px solid {BRAND["line"]};vertical-align:top;font-size:14px}}
.rw-detail td:first-child{{width:132px;color:{BRAND["muted"]};white-space:nowrap;font-weight:600}}
.rw-empty{{padding:28px;text-align:center;color:{BRAND["faint"]};background:#FBFCFE;
  border:1px dashed {BRAND["line"]};border-radius:12px;font-size:14px}}

/* ── 分区标题与筛选摘要 ── */
.rw-section{{margin:22px 0 10px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}}
.rw-section-title{{font-size:1.15rem;font-weight:700;color:{BRAND["primary"]};line-height:1.3}}
.rw-section-caption{{font-size:13px;color:{BRAND["muted"]}}}
.rw-filter-bar{{
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  background:{BRAND["card"]};border:1px solid {BRAND["line"]};border-radius:12px;
  padding:10px 14px;margin:4px 0 14px;box-shadow:0 1px 4px rgba(15,23,42,.04)
}}
.rw-filter-count{{font-weight:700;color:{BRAND["primary"]};font-size:14.5px;white-space:nowrap}}
.rw-filter-chip{{
  display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:600;
  background:#EEF2FF;color:{BRAND["primary"]};border:1px solid #DBE4FB
}}
.rw-filter-empty{{font-size:13px;color:{BRAND["faint"]}}}
.rw-toolbar{{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:2px 0 8px}}
.rw-link{{color:{BRAND["accent"]};text-decoration:none;font-weight:600}}
.rw-link:hover{{text-decoration:underline}}

/* ── 筛选字段：标签在左、控件在右 ── */
.rw-field-label{{
  font-size:13.5px;font-weight:600;color:{BRAND["muted"]};
  padding-top:.55rem;line-height:1.3;
}}
.rw-field-hint{{font-size:12px;color:{BRAND["faint"]};margin-top:2px}}

/* ── Streamlit 控件：边框更明显，便于识别可交互 ── */
div[data-testid="stMetric"]{{background:{BRAND["card"]};border:1px solid {BRAND["line"]};
  border-radius:12px;padding:14px 16px;box-shadow:0 2px 8px rgba(15,23,42,.04)}}
div[data-testid="stMetric"] label{{color:{BRAND["muted"]} !important;font-size:13px !important;font-weight:600 !important}}
div[data-testid="stMetricValue"]{{color:{BRAND["primary"]} !important;font-weight:700 !important;font-size:1.6rem !important}}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  border-radius:10px;font-weight:600;border:1px solid #C9D4E8;
  transition:transform .15s ease, box-shadow .15s ease;
  font-size:14px;
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
  transform:translateY(-1px);box-shadow:0 6px 16px rgba(37,99,235,.16)
}}
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible,
.stFormSubmitButton > button:focus-visible {{
  outline:2px solid {BRAND["accent"]};outline-offset:2px
}}
.stTextInput input:focus-visible, .stTextArea textarea:focus-visible,
div[data-baseweb="select"]:focus-within > div {{
  outline:none
}}
.stButton > button[kind="primary"], .stFormSubmitButton > button {{
  background:linear-gradient(135deg,{BRAND["accent"]},{BRAND["sky"]});
  border:none;color:#fff
}}
div[data-testid="stDataFrame"]{{border:1px solid {BRAND["line"]};border-radius:12px;overflow:hidden;
  font-size:14px}}
div[data-testid="stExpander"]{{border:1px solid {BRAND["line"]};border-radius:12px;background:{BRAND["card"]}}}
div[data-testid="stExpander"] summary{{font-weight:600;font-size:14.5px}}
div[data-baseweb="tab"]{{font-size:14.5px}}
div[data-baseweb="tab-highlight"]{{background:{BRAND["accent"]}}}
.stTabs [data-baseweb="tab-list"]{{gap:6px}}
.stTabs [data-baseweb="tab"]{{padding:6px 14px;border-radius:9px 9px 0 0}}

/* 文本 / 多选 / 下拉 / 日期：浅填充 + 可见描边 */
.stTextInput input, .stTextArea textarea, .stNumberInput input {{
  border:1.5px solid #C7D2E5 !important;
  border-radius:10px !important;
  background:#fff !important;
  color:{BRAND["text"]} !important;
  font-size:14.5px !important;
}}
.stTextInput input:focus, .stTextArea textarea:focus {{
  border-color:{BRAND["accent"]} !important;
  box-shadow:0 0 0 3px rgba(37,99,235,.15) !important;
}}
div[data-baseweb="select"] > div {{
  border:1.5px solid #C7D2E5 !important;
  border-radius:10px !important;
  background:#fff !important;
  min-height:40px;
}}
div[data-baseweb="select"]:hover > div {{border-color:#8FB0F0 !important}}
.stMultiSelect [data-baseweb="tag"] {{
  background:#EEF2FF !important;
  border-radius:8px !important;
}}
.stMultiSelect [data-baseweb="tag"] span {{
  color:{BRAND["primary"]} !important;
  font-weight:600;
}}
.stDateInput input {{
  border:1.5px solid #C7D2E5 !important;
  border-radius:10px !important;
  background:#fff !important;
}}
.stProgress > div > div{{background:linear-gradient(90deg,{BRAND["accent"]},{BRAND["sky"]})}}
[data-testid="stStatusWidget"]{{display:none}}
footer{{visibility:hidden}}
/* 数据表：略紧凑、表头更清晰 */
.element-container div[data-testid="stDataFrame"] {{
  font-size:13.5px;
}}
/* 分区间距节奏：正文区块之间更均匀 */
.element-container + .element-container {{margin-top:.15rem}}
@media (prefers-reduced-motion: reduce) {{
  .rw-card,.stButton > button,.stDownloadButton > button,.stFormSubmitButton > button {{
    animation:none !important;transition:none !important
  }}
  .rw-card:hover,.stButton > button:hover,.stDownloadButton > button:hover,
  .stFormSubmitButton > button:hover {{transform:none}}
}}
"""


def apply_theme() -> None:
    """注入全局主题样式（幂等，可在每个页面调用）。"""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


def parse_date_arg(value: object) -> date | None:
    """把会话/组件里可能的日期形态归一化为 ``date``；无法解析时返回 ``None``。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = parse_date_arg(item)
            if parsed is not None:
                return parsed
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _date_to_session(value: object) -> str:
    """把 ``st.date_input`` 的返回值写成会话状态用的 ISO 字符串。"""
    parsed = parse_date_arg(value)
    return parsed.isoformat() if parsed else ""


def field(label: str, render: Callable[[], Any], *, hint: str = "") -> Any:
    """「标签在左、控件在右」的紧凑字段行；返回控件的取值。"""
    left, right = st.columns([1.05, 2.5], vertical_alignment="center")
    with left:
        caption = f'<div class="rw-field-hint">{html.escape(hint)}</div>' if hint else ""
        st.markdown(
            f'<div class="rw-field-label">{html.escape(label)}</div>{caption}',
            unsafe_allow_html=True,
        )
    with right:
        return render()


def date_input_field(
    label: str,
    state_key: str,
    *,
    widget_key: str,
    help: str = "",
) -> str:
    """可选日期筛选：空值表示不限；写回会话为 ``YYYY-MM-DD`` 或空串。"""
    from regwatch.web.state import get, set_value

    current = parse_date_arg(get(state_key, ""))
    picked = st.date_input(
        label,
        value=current,
        format="YYYY-MM-DD",
        key=widget_key,
        help=help or "留空表示不限制日期",
        label_visibility="collapsed",
    )
    iso = _date_to_session(picked)
    set_value(state_key, iso)
    return iso


def page_header(title: str, subtitle: str = "", chips: Iterable[str] = ()) -> None:
    """页面标题区：大标题 + 说明 + 可选标签。"""
    chips_html = "".join(f'<span class="rw-chip">{html.escape(chip)}</span>' for chip in chips)
    st.markdown(
        f'<div style="margin-bottom:14px">'
        f'<div class="rw-title" style="color:{BRAND["primary"]}">{html.escape(title)}</div>'
        + (
            f'<div style="color:{BRAND["muted"]};margin-top:4px;font-size:15px">{html.escape(subtitle)}</div>'
            if subtitle
            else ""
        )
        + (f'<div style="margin-top:8px">{chips_html}</div>' if chips_html else "")
        + "</div>",
        unsafe_allow_html=True,
    )


def section_header(title: str, caption: str = "") -> None:
    """统一的分区标题（替代散落的四级 markdown 标题）。"""
    caption_html = (
        f'<span class="rw-section-caption">{html.escape(caption)}</span>' if caption else ""
    )
    st.markdown(
        f'<div class="rw-section">'
        f'<div class="rw-section-title">{html.escape(title)}</div>'
        f"{caption_html}</div>",
        unsafe_allow_html=True,
    )


def safe_url(url: object) -> str:
    """仅放行 http(s) 链接；其它一律返回空串，避免注入或危险协议。"""
    text = str(url or "").strip()
    lowered = text.lower()
    if lowered.startswith("https://") or lowered.startswith("http://"):
        return text
    return ""


def source_link(url: object, label: str = "打开原文") -> str:
    """生成来源链接 HTML；非法 URL 显示占位。"""
    href = safe_url(url)
    if not href:
        return html.escape(_EMPTY_DISPLAY)
    return (
        f'<a class="rw-link" href="{html.escape(href, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'
    )


def filter_summary(total: int, chips: Sequence[str] = (), *, unit: str = "条") -> None:
    """结果上方摘要条：命中数量 + 当前筛选条件 chips。"""
    chip_html = "".join(
        f'<span class="rw-filter-chip">{html.escape(str(chip))}</span>'
        for chip in chips
        if str(chip).strip()
    )
    content = chip_html or '<span class="rw-filter-empty">未设置筛选条件</span>'
    st.markdown(
        f'<div class="rw-filter-bar">'
        f'<span class="rw-filter-count">命中 {total:,} {unit}</span>'
        f"{content}</div>",
        unsafe_allow_html=True,
    )


def csv_bytes(frame: Any) -> bytes:
    """DataFrame 导出为带 BOM 的 CSV，便于 Excel 打开中文列名。"""
    return frame.to_csv(index=False).encode("utf-8-sig")


def download_button(
    frame: Any,
    *,
    filename: str,
    label: str = "导出 CSV",
    key: str | None = None,
) -> None:
    """统一 CSV 下载按钮。"""
    st.download_button(
        label,
        data=csv_bytes(frame),
        file_name=filename,
        mime="text/csv",
        key=key,
        width="stretch",
    )


def metric_cards(
    items: Sequence[dict[str, Any]],
    columns: int | None = None,
) -> None:
    """渲染一排指标卡。

    每个 item 支持：``label`` / ``value`` / ``hint`` / ``tone``（primary|amber|ok|danger）。
    """
    if not items:
        return
    cols = columns or min(len(items), 4)
    cards: list[str] = []
    for item in items:
        tone = str(item.get("tone", "primary"))
        value_color = _TONE_VALUE_COLORS.get(tone, BRAND["primary"])
        cards.append(
            f'<div class="rw-card{" rw-" + tone if tone and tone != "primary" else ""}">'
            f'<div class="rw-label">{html.escape(str(item.get("label", "")))}</div>'
            f'<div class="rw-value" style="color:{value_color}">'
            f"{html.escape(str(item.get('value', '')))}</div>"
            f'<div class="rw-hint">{html.escape(str(item.get("hint", "")))}</div>' + "</div>"
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
    return (
        f'<span class="rw-badge" style="background:{bg};color:{color}">{html.escape(text)}</span>'
    )


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
        f'<div class="rw-empty"><div style="font-size:15.5px;font-weight:600;margin-bottom:6px">'
        f'{html.escape(text)}</div><div style="font-size:13.5px">{html.escape(hint)}</div></div>',
        unsafe_allow_html=True,
    )


def detail_rows(pairs: Sequence[tuple[str, Any]], *, raw_keys: Iterable[str] = ()) -> str:
    """键值对详情表 HTML。

    ``raw_keys`` 中的字段 value 视为已生成的受信任 HTML（如 :func:`source_link`）；
    其余字段一律转义，避免标题/摘要里的尖括号被当成标签。
    """
    raw = set(raw_keys)
    cells: list[str] = []
    for key, value in pairs:
        key_text = str(key)
        if key_text in raw:
            cell = str(value) if value not in (None, "") else _EMPTY_DISPLAY
        else:
            cell = html.escape(str(value)) if value not in (None, "") else _EMPTY_DISPLAY
        cells.append(f"<tr><td>{html.escape(key_text)}</td><td>{cell}</td></tr>")
    return f'<table class="rw-detail">{"".join(cells)}</table>'
