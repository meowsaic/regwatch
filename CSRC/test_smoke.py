"""兼容入口：CSRC 冒烟测试已迁移到 ``tests/test_bureaus.py`` 与 ``tests/test_sources.py``。

运行方式（在项目根目录）：``python -m unittest discover -s tests -t .``
本文件保留以兼容旧 habit，执行时会转发到新测试。
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
    suite = unittest.TestSuite(
        loader.loadTestsFromName(name)
        for name in ("tests.test_bureaus", "tests.test_sources")
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
