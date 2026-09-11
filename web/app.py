"""regwatch 网页端入口。

五个页面：总览看板、案例浏览、统计分析、任务中心、模型与配置。

启动方式：``python -m regwatch.cli web`` 或 ``python run_web.py``。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent
for _path in (str(_PROJECT_ROOT), str(_HERE.parent)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from components import ui  # noqa: E402
from components.data import clear_data_cache, load_stats  # noqa: E402
from regwatch.jobs import get_job_manager  # noqa: E402
from regwatch.logutil import configure_logging  # noqa: E402

st.set_page_config(
    page_title="regwatch · 基金监管案例看板",
    page_icon=":material/shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.apply_theme()
configure_logging()

with st.sidebar:
    st.markdown(
        '<div style="padding:4px 2px 12px">'
        '<div style="font-size:23px;font-weight:700;letter-spacing:.5px">基金监管案例看板</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "数据来源：中国证券投资基金业协会（AMAC）纪律处分，"
        "中国证监会及 37 家派出机构的行政处罚与监管措施。"
    )
    st.divider()
    if st.button("刷新数据缓存", width="stretch"):
        clear_data_cache()
        st.toast("数据缓存已刷新")

try:
    stats = load_stats()
    basic = stats.get("basic", {})
    violation_count = len(stats.get("violation_distribution") or [])
    chips = [
        f"案例总数 {basic.get('total', 0):,}",
        f"机构 {basic.get('institution_count', 0):,} / 个人 {basic.get('personnel_count', 0):,}",
        f"违规类型 {violation_count} 种",
        f"运行中任务 {get_job_manager().running_count()}",
    ]
    st.markdown(
        '<div class="rw-panel" style="display:flex;gap:18px;align-items:center;'
        'flex-wrap:wrap;padding:10px 18px;margin-bottom:6px">'
        + "".join(f'<span style="font-size:12.5px;color:#475569">{item}</span>' for item in chips)
        + "</div>",
        unsafe_allow_html=True,
    )
except Exception as exc:  # noqa: BLE001 - 首次运行数据缺失时也不阻塞导航
    st.caption(f"数据加载提示：{exc}")

pages = [
    st.Page("views/overview.py", title="总览看板", icon=":material/dashboard:", default=True),
    st.Page("views/cases.py", title="案例浏览", icon=":material/travel_explore:"),
    st.Page("views/statistics.py", title="统计分析", icon=":material/insights:"),
    st.Page("views/jobs.py", title="任务中心", icon=":material/rocket_launch:"),
    st.Page("views/settings.py", title="模型与配置", icon=":material/tune:"),
]
st.navigation(pages).run()
