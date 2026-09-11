"""兼容入口：CSRC 案例抓取实现已迁移到 regwatch 包。

实际实现：``regwatch/sources/csrc.py`` 与 ``regwatch/sources/csrc_bureaus.py``
推荐命令：``python -m regwatch.cli fetch csrc --start 2022-01-01``
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.logutil import configure_logging  # noqa: E402
from regwatch.sources import csrc  # noqa: E402,F401

if __name__ == "__main__":
    configure_logging()
    print("提示：实现已迁移到 regwatch 包，等效命令 python -m regwatch.cli fetch csrc")
    result = csrc.fetch()
    print("完成：成功 {a}，失败 {b}，跳过非基金 {c}".format(a=result.success, b=result.failed, c=result.skipped))
