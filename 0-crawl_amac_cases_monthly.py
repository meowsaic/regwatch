"""兼容入口：AMAC 月度公告 PDF 爬虫已迁移到 regwatch 包。

实际实现：``regwatch/sources/amac_monthly.py``
推荐命令：``python -m regwatch.cli fetch monthly``
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.logutil import configure_logging  # noqa: E402
from regwatch.sources import amac_monthly  # noqa: E402,F401

if __name__ == "__main__":
    configure_logging()
    print("提示：实现已迁移到 regwatch 包，等效命令 python -m regwatch.cli fetch monthly")
    summary = amac_monthly.download_last_month()
    print("下载完成：成功 {a}，失败 {b}".format(a=summary.get("success", 0), b=summary.get("failed", 0)))
