"""Streamlit 网页端入口。

导航用 ``st.navigation`` + ``st.Page`` 声明式组织（不再是 if/else 拼页面），
五个页面对应：总览、案例浏览、统计分析、任务中心、模型与配置。
"""

from __future__ import annotations

import streamlit as st

from regwatch.domain import CaseQuery
from regwatch.logging_setup import configure_logging
from regwatch.web.components import ui
from regwatch.web.components.data import (
    cache_key,
    clear_data_cache,
    load_overview,
    settings,
)

configure_logging()

st.set_page_config(
    page_title="regwatch · 基金监管案例看板",
    page_icon="🛡",
    layout="wide",
    initial_sidebar_state="expanded",
)
ui.apply_theme()


def _sidebar() -> None:
    with st.sidebar:
        st.markdown("### 🛡 regwatch")
        cfg = settings()
        st.caption(f"数据库：{cfg.database.name}")
        overview = load_overview(_overview_key())
        st.metric("案例总数", f"{overview['total']:,}")
        st.caption(
            f"已提取 {overview['status'].get('done', 0)} · "
            f"待提取 {overview['status'].get('pending', 0)}"
        )
        if st.button("刷新数据缓存", width="stretch"):
            clear_data_cache()
            st.rerun()
        st.divider()
        st.caption("网页端与命令行共用同一套用例层与数据库。")


def _overview_key() -> tuple:
    """总览页使用不带筛选的查询（缓存键里含数据版本号）。"""
    return cache_key(CaseQuery())


def main() -> None:
    from regwatch.web.views import cases, jobs, overview, settings_view, statistics

    # 各视图入口都叫 render，必须显式给 url_path，否则 Streamlit 会按函数名
    # 推断出重复的 pathname（"render"）而报 Multiple Pages 异常。
    pages = [
        st.Page(overview.render, title="总览看板", icon="📊", url_path="overview", default=True),
        st.Page(cases.render, title="案例浏览", icon="🔍", url_path="cases"),
        st.Page(statistics.render, title="统计分析", icon="📈", url_path="statistics"),
        st.Page(jobs.render, title="任务中心", icon="⚙", url_path="jobs"),
        st.Page(settings_view.render, title="模型与配置", icon="🧩", url_path="settings"),
    ]
    navigation = st.navigation(pages)
    _sidebar()
    navigation.run()


if __name__ == "__main__":
    main()
else:  # pragma: no cover - Streamlit 以脚本方式执行时会走此分支
    main()
