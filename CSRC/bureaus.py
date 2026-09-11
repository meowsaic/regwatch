"""兼容入口：证监局配置已迁移到 regwatch 包。

实际实现：``regwatch/sources/csrc_bureaus.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from regwatch.sources.csrc_bureaus import (  # noqa: E402,F401
    BUREAUS,
    Bureau,
    discover_penalty_url,
    get_bureau_by_code,
    get_bureau_by_name,
)
