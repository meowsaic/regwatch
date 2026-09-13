"""Streamlit 网页端入口（本地 ``regwatch web`` 使用）。

导航用 ``st.navigation`` + ``st.Page`` 声明式组织，页面清单见 :mod:`regwatch.web.nav`：
总览、案例浏览、统计分析、智能问答、任务中心、模型与配置。装配逻辑在 :mod:`regwatch.web.shell`。

云端公开部署请改用 ``deploy/streamlit_public.py``（只挂四个只读页面）。
"""

from __future__ import annotations

from regwatch.web import nav, shell

# set_page_config 必须是脚本里的第一条 Streamlit 命令
shell.configure_page()


def main() -> None:
    shell.run(nav.visible_items())


if __name__ == "__main__":
    main()
else:  # pragma: no cover - Streamlit 以脚本方式执行时会走此分支
    main()
