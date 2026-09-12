"""Streamlit Community Cloud 公开只读入口。

在 Community Cloud 的 **Main file path** 填本文件，得到的是一个**结构上只读**的看板：
只注册 总览看板 / 案例浏览 / 统计分析 三个页面，任务中心与「模型与配置」**根本不进导航**
—— 不看环境变量、不需要口令，也不存在被误配打开的可能。

两个入口怎么选：

| 想部署成什么 | Main file path |
|--------------|----------------|
| 公开只读看板（不用记任何口令） | ``deploy/streamlit_public.py`` |
| 自用 / 私有，云端也要跑任务与配置（默认只读，口令解锁） | ``deploy/streamlit_app.py`` |

本地开发不受影响：``uv run regwatch web`` 仍然是五个页面全开。
依赖清单 ``deploy/requirements.txt`` 与入口同目录，两个入口共用。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from regwatch.web import nav, shell  # noqa: E402
from regwatch.web.cloud import prepare_runtime  # noqa: E402

# set_page_config 必须是脚本里的第一条 Streamlit 命令
shell.configure_page()

# Secrets 仍会桥接（例如 database_url 下载数据库），只是本入口没有写配置的页面
prepare_runtime()

shell.run(
    nav.public_items(),
    note="只读演示：数据是仓库快照，更新由维护者在本地完成后 push。",
)
