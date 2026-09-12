"""网页端外壳：页面配置、侧边栏与 ``st.navigation`` 装配。

两个入口共用这一层，差别只在「挂哪几个页面」：

- ``src/regwatch/web/app.py``：本地 ``regwatch web`` 用，按权限挂
  :func:`regwatch.web.nav.visible_items`（五个页面）；
- ``deploy/streamlit_public.py``：云端公开部署用，固定只挂
  :func:`regwatch.web.nav.public_items`（总览/案例/统计/智能问答）。

调用顺序有讲究：入口脚本**必须先调 :func:`configure_page`**（``st.set_page_config``
要是脚本里的第一条 Streamlit 命令），再去做下载数据库之类会渲染 spinner 的准备工作，
最后调 :func:`run`。
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

import streamlit as st

from ..domain import CaseQuery
from ..logging_setup import configure_logging
from . import access, nav
from .components import ui
from .components.data import cache_key, clear_data_cache, load_overview, settings

__all__ = ["build_pages", "configure_page", "run", "sidebar"]


def configure_page() -> None:
    """进程级页面配置与主题；必须是每次脚本运行的第一条 Streamlit 命令。"""
    configure_logging()
    st.set_page_config(
        page_title="regwatch · 基金监管案例看板",
        page_icon="🛡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    ui.apply_theme()


def _overview_key() -> tuple:
    """总览页使用不带筛选的查询（缓存键里含数据版本号）。"""
    return cache_key(CaseQuery())


def sidebar(note: str = "") -> None:
    """统一侧边栏：紧凑规模信息、刷新缓存、只读提示（可选 ``note`` 说明）。"""
    with st.sidebar:
        st.markdown("### 🛡 regwatch")
        cfg = settings()
        st.caption(f"数据库：{cfg.database.name}")
        overview = load_overview(_overview_key())
        # 用紧凑 caption 替代 st.metric 大卡：侧边栏更窄，深色底上对比度也更稳
        st.markdown(
            f"案例总数 **{overview['total']:,}**  "
            f"已提取 {overview['status'].get('done', 0)}  ·  "
            f"待提取 {overview['status'].get('pending', 0)}"
        )
        date_min = str(overview.get("date_min") or "")
        date_max = str(overview.get("date_max") or "")
        if date_min or date_max:
            st.caption(f"数据范围：{date_min or '—'} ~ {date_max or '—'}")
        if st.button("刷新数据缓存", width="stretch"):
            clear_data_cache()
            st.rerun()
        st.divider()
        access.sidebar_lock()
        if note:
            st.caption(note)
        st.caption("网页端与命令行共用同一套用例层与数据库。")


def build_pages(items: Iterable[nav.NavItem]) -> list[st.Page]:
    """把导航项装配成 ``st.Page`` 列表。

    各视图入口都叫 ``render``，必须显式给 ``url_path``，否则 Streamlit 会按函数名
    推断出重复的 pathname（"render"）而报 Multiple Pages 异常。
    """
    pages: list[st.Page] = []
    for item in items:
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
    return pages


def run(items: Iterable[nav.NavItem] | None = None, *, note: str = "") -> None:
    """装配并运行网页端；``items`` 留空时按当前权限取导航项。"""
    selected = nav.visible_items() if items is None else tuple(items)
    navigation = st.navigation(build_pages(selected))
    sidebar(note)
    navigation.run()
