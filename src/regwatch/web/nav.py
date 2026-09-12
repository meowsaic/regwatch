"""导航定义：哪些页面出现、哪些只对管理员开放。

抽成独立模块的理由：

1. ``app.py`` 是 Streamlit 的入口脚本，import 它会执行 ``main()``，不适合被测试引用；
2. 「云端只读时把哪几页收起来」属于权限策略，和 :mod:`regwatch.web.access` 一起演进。

默认行为：本地 ``regwatch web`` 五个页面全开；``deploy/streamlit_app.py`` 打开的
只读模式（``REGWATCH_READ_ONLY=1``）下，``admin_only`` 的页面**直接从导航里消失**，
点不到；在侧边栏输入 ``REGWATCH_ADMIN_TOKEN`` 口令解锁后才会重新出现。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import access

__all__ = ["DEFAULT_URL_PATH", "NAV_ITEMS", "NavItem", "public_items", "visible_items"]

#: 默认首页的 url_path
DEFAULT_URL_PATH = "overview"


@dataclass(frozen=True, slots=True)
class NavItem:
    """一个导航项（``module`` 是 ``regwatch.web.views`` 下的模块名）。"""

    module: str
    title: str
    icon: str
    url_path: str
    admin_only: bool = False


NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("overview", "总览看板", "📊", DEFAULT_URL_PATH),
    NavItem("cases", "案例浏览", "🔍", "cases"),
    NavItem("statistics", "统计分析", "📈", "statistics"),
    NavItem("jobs", "任务中心", "⚙", "jobs", admin_only=True),
    # 模块名是 settings；views/__init__.py 里的 settings_view 只是对外别名
    NavItem("settings", "模型与配置", "🧩", "settings", admin_only=True),
)


def public_items() -> tuple[NavItem, ...]:
    """只读页面（三个）：公开入口固定挂这几页，与开关和权限无关。"""
    return tuple(item for item in NAV_ITEMS if not item.admin_only)


def visible_items(admin: bool | None = None) -> tuple[NavItem, ...]:
    """按权限过滤导航项。

    Args:
        admin: 显式指定权限（测试用）；留空时按 :func:`regwatch.web.access.is_admin`。
    """
    allowed = access.is_admin() if admin is None else admin
    return NAV_ITEMS if allowed else public_items()
