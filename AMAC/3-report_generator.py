"""兼容入口：AMAC 季度报告实现已迁移到 regwatch 包。

实际实现：``regwatch/analyze.py`` 与 ``regwatch/report.py``
推荐命令：``python -m regwatch.cli report --dataset amac``，加 --llm 可让模型撰写合规建议
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch import report as report_mod  # noqa: E402
from regwatch.config import get_config  # noqa: E402
from regwatch.logutil import configure_logging  # noqa: E402
from regwatch.storage import DATASET_AMAC  # noqa: E402,F401

if __name__ == "__main__":
    configure_logging()
    print("提示：实现已迁移到 regwatch 包，等效命令 python -m regwatch.cli report --dataset amac")
    output = report_mod.build_report(DATASET_AMAC, use_llm=False)
    directory = get_config().data_root("amac_reports")
    stem = "AMAC_季度分析报告_" + datetime.now().strftime("%Y%m%d_%H%M")
    paths = report_mod.save_report_files(output, directory, stem=stem)
    print("报告已生成（{} 条案例）：".format(output.row_count))
    for kind, path in paths.items():
        print("  {kind}: {path}".format(kind=kind, path=path))
