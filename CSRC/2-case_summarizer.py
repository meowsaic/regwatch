"""兼容入口：CSRC 结构化摘要实现已迁移到 regwatch 包。

实际实现：``regwatch/summarize.py``
推荐命令：``python -m regwatch.cli summarize --dataset csrc``
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.logutil import configure_logging  # noqa: E402
from regwatch.storage import DATASET_CSRC  # noqa: E402,F401
from regwatch.summarize import summarize  # noqa: E402,F401

if __name__ == "__main__":
    configure_logging()
    print("提示：实现已迁移到 regwatch 包，等效命令 python -m regwatch.cli summarize --dataset csrc")
    result = summarize(DATASET_CSRC)
    print("完成：成功 {a}，跳过 {b}，失败 {c}".format(a=result.success, b=result.skipped, c=result.failed))
