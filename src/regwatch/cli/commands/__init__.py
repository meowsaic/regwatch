"""命令行子命令集合。"""

from __future__ import annotations

from .config_cmd import app as config_app
from .db_cmd import app as db_app
from .fetch import app as fetch_app
from .orgtype_cmd import app as org_type_app
from .report_cmd import app as report_app
from .summarize_cmd import app as summarize_app

__all__ = [
    "config_app",
    "db_app",
    "fetch_app",
    "org_type_app",
    "report_app",
    "summarize_app",
]
