"""兼容入口：AMAC 机构类型回填实现已迁移到 regwatch 包。

实际实现：``regwatch/org_type.py``
推荐命令：``python -m regwatch.cli org-type --dry-run`` 或 ``--interactive``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.logutil import configure_logging  # noqa: E402
from regwatch.org_type import backfill_org_types, interactive_fill  # noqa: E402,F401

if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(description="为 AMAC 机构类案例补齐机构登记类型")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入文件")
    parser.add_argument("--interactive", action="store_true", help="进入交互式终端补全")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条，0 表示全部")
    args = parser.parse_args()

    print("提示：实现已迁移到 regwatch 包，等效命令 python -m regwatch.cli org-type")
    if args.interactive:
        filled, skipped = interactive_fill()
        print("已补全 {a} 条，跳过 {b} 条".format(a=filled, b=skipped))
    else:
        result = backfill_org_types(dry_run=args.dry_run, limit=args.limit or None)
        print(result.to_dict())
