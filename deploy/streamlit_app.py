"""Streamlit Community Cloud 部署入口。

部署时在 Community Cloud 的 **Main file path** 填写本文件：``deploy/streamlit_app.py``。

为什么不直接把 ``src/regwatch/web/app.py`` 填进去：

1. 仓库是 src 布局，云端不会安装本项目，所以这里先把 ``src/`` 加入 ``sys.path``，
   省掉 ``pip install -e .``（云端对可编辑安装没有明确保证）；
2. Community Cloud 只使用「入口脚本所在目录 → 仓库根目录」中找到的**第一个**依赖
   文件，同目录内的优先级为 ``uv.lock`` > ``Pipfile`` > ``environment.yml`` >
   ``requirements.txt`` > ``pyproject.toml``。仓库根目录有本地开发用的 ``uv.lock``，
   它会盖掉根目录的 ``requirements.txt``；把 :file:`deploy/requirements.txt` 放在入口
   旁边，就能确定性地只装这份清单里的依赖。

本地等价启动方式：``uv run regwatch web``（或 ``streamlit run deploy/streamlit_app.py``）。
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from regwatch.web import app_path  # noqa: E402
from regwatch.web.cloud import prepare_runtime  # noqa: E402

# 云端没有 config.json / data/regwatch.db，密钥与数据库都从应用 Secrets 注入
prepare_runtime()

# app.py 只在以脚本方式执行（__name__ == "__main__"）时才调用 main()，
# 因此用 runpy 按脚本执行，而不是 import（import 会走模块内的 else 分支）。
runpy.run_path(str(app_path()), run_name="__main__")
