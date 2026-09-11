"""兼容入口：AMAC 案例抓取实现已迁移到 regwatch 包。

实际实现：``regwatch/sources/amac.py``
推荐命令：``python -m regwatch.cli fetch amac --start 2026-01-01 --end 2026-03-31``
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.logutil import configure_logging  # noqa: E402
from regwatch.sources import amac  # noqa: E402,F401

if __name__ == "__main__":
    configure_logging()
    print("提示：实现已迁移到 regwatch 包，等效命令 python -m regwatch.cli fetch amac")
    result = amac.fetch()
    print("完成：成功 %d，失败 %d" % (result.success, result.failed))
