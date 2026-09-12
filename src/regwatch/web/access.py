"""网页端访问控制：云端只读模式与管理员解锁。

Streamlit 自身没有登录体系，这里只做最薄的一层防护：把「会写容器数据、会花模型额度、
会把密钥发到外部端点」的操作收起来，避免公开部署被陌生人当免费算力和密钥探针用。

两个开关都走环境变量（云端可写在应用 Secrets 里，见 ``web/cloud.py`` 的桥接规则）：

- ``REGWATCH_READ_ONLY=1``：任务中心与「模型与配置」变成只读。
  ``deploy/streamlit_app.py`` 在云端默认打开它，本地 ``regwatch web`` 不受影响；
- ``REGWATCH_ADMIN_TOKEN=<口令>``：额外提供解锁入口，输入口令后**当前浏览器会话**
  恢复全部功能（口令只放在 ``session_state``，不落盘、不进日志）。
"""

from __future__ import annotations

import hmac
import os
from typing import Final

import streamlit as st

__all__ = [
    "ADMIN_TOKEN_ENV",
    "READ_ONLY_ENV",
    "admin_token",
    "is_admin",
    "read_only",
    "require_admin",
    "sidebar_lock",
]

READ_ONLY_ENV: Final = "REGWATCH_READ_ONLY"
ADMIN_TOKEN_ENV: Final = "REGWATCH_ADMIN_TOKEN"

_UNLOCK_KEY: Final = "regwatch.admin_unlocked"
_TOKEN_INPUT_KEY: Final = "regwatch.admin-token"
_UNLOCK_BUTTON_KEY: Final = "regwatch.admin-unlock"

# 侧边栏与页面内可能同时渲染解锁表单，键名错开避免 DuplicateWidgetID
_SIDEBAR_TOKEN_KEY: Final = "regwatch.admin-token@sidebar"
_SIDEBAR_BUTTON_KEY: Final = "regwatch.admin-unlock@sidebar"

_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})

#: 只读模式下的页面说明（键为 require_admin 传入的页面标识）
_NOTICES: Final = {
    "jobs": (
        "当前为**只读模式**：云端实例跑的任务只写容器内的临时副本，不会回写 GitHub，"
        "实例休眠或重启后即消失。要更新数据，请在本地运行 `regwatch fetch` / `summarize` "
        "后 push，云端会自动重新部署。"
    ),
    "settings": (
        "当前为**只读模式**：模型密钥、任务绑定与数据路径由应用 Secrets 统一管理，"
        "页面不再提供修改入口（云端改动重启即失效）。"
    ),
}


def read_only() -> bool:
    """当前进程是否处于只读模式（云端入口默认打开）。"""
    return os.environ.get(READ_ONLY_ENV, "").strip().lower() in _TRUTHY


def admin_token() -> str:
    """管理员口令；未配置时返回空串。"""
    return os.environ.get(ADMIN_TOKEN_ENV, "").strip()


def _unlocked() -> bool:
    """本会话是否已解锁（没有 Streamlit 运行时则视为未解锁）。"""
    try:
        return bool(st.session_state.get(_UNLOCK_KEY))
    except Exception:  # pragma: no cover - 裸跑（测试/脚本）时没有 session_state
        return False


def is_admin() -> bool:
    """当前会话能否执行写操作。"""
    if not read_only():
        return True
    return bool(admin_token()) and _unlocked()


def require_admin(page: str) -> bool:
    """写操作页面的统一入口。

    Returns:
        放行返回 ``True``；只读时渲染说明（配置了口令则附解锁表单）并返回 ``False``。
    """
    if is_admin():
        return True

    st.info(_NOTICES.get(page, "当前为只读模式。"))

    token = admin_token()
    if not token:
        return False

    with st.form("admin-unlock", border=True):
        st.caption("管理员解锁：输入口令后，本浏览器会话恢复全部功能")
        value = st.text_input("口令", type="password", key=_TOKEN_INPUT_KEY)
        submitted = st.form_submit_button("解锁", key=_UNLOCK_BUTTON_KEY)

    if submitted:
        if hmac.compare_digest(value.strip(), token):
            st.session_state[_UNLOCK_KEY] = True
            st.rerun()
        else:
            st.error("口令不正确")
    return False


def sidebar_lock() -> None:
    """侧边栏里的只读提示与解锁入口（只在只读模式下渲染）。

    导航项由 :func:`regwatch.web.nav.visible_items` 在每次脚本重跑时重新计算，
    因此这里解锁后 ``st.rerun()`` 会让「任务中心」「模型与配置」重新出现。
    """
    if not read_only():
        return

    if is_admin():
        st.caption("🔓 已解锁：任务中心与「模型与配置」可用")
        return

    st.caption("🔒 云端只读：任务中心与「模型与配置」已隐藏")

    token = admin_token()
    if not token:
        return

    with st.form("admin-unlock-sidebar", border=False):
        value = st.text_input(
            "管理员口令",
            type="password",
            key=_SIDEBAR_TOKEN_KEY,
            label_visibility="collapsed",
            placeholder="管理员口令",
        )
        submitted = st.form_submit_button("解锁", key=_SIDEBAR_BUTTON_KEY)

    if submitted:
        if hmac.compare_digest(value.strip(), token):
            st.session_state[_UNLOCK_KEY] = True
            st.rerun()
        else:
            st.error("口令不正确")
