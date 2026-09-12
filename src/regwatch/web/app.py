"""Streamlit 网页端入口。

导航用 ``st.navigation`` + ``st.Page`` 声明式组织（不再是 if/else 拼页面），
五个页面对应：总览、案例浏览、统计分析、任务中心、模型与配置。
"""

from __future__ import annotations

import streamlit as st

from regwatch.domain import CaseQuery
from regwatch.logging_setup import configure_logging
from regwatch.web import access, nav
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
        access.sidebar_lock()
        st.caption("网页端与命令行共用同一套用例层与数据库。")


def _overview_key() -> tuple:
    """总览页使用不带筛选的查询（缓存键里含数据版本号）。"""
    return cache_key(CaseQuery())


def main() -> None:
    import importlib

    # 各视图入口都叫 render，必须显式给 url_path，否则 Streamlit 会按函数名
    # 推断出重复的 pathname（"render"）而报 Multiple Pages 异常。
    # 页面清单由 nav 决定：云端只读时，任务中心与「模型与配置」直接不出现（点不到），
    # 在侧边栏用 REGWATCH_ADMIN_TOKEN 解锁后才会重新出现。
    pages = []
    for item in nav.visible_items():
        module = importlib.import_module(f"regwatch.web.views.{item.module}")
        pages.append(
            st.Page(
                module.render,
                title=item.title,
                icon=item.icon,
                url_path=item.url_path,
                default=item.url_path == nav.DEFAULT_URL_PATH,
            )
        )
    navigation = st.navigation(pages)
    _sidebar()
    navigation.run()


if __name__ == "__main__":
    main()
else:  # pragma: no cover - Streamlit 以脚本方式执行时会走此分支
    main()
