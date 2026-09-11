"""兼容入口：实网连通性测试已迁移到 ``tests/test_live.py``（默认跳过）。

运行方式（在项目根目录）：先设置环境变量 REGWATCH_LIVE_TEST=1，再执行
``python -m unittest tests.test_live -v``
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_live")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
