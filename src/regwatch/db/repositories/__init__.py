"""数据仓储集合。

四个仓储各管一组表，统一由 :class:`~regwatch.db.connection.Database` 提供连接。
"""

from __future__ import annotations

from .cases import CaseRepository
from .meta import MetaRepository
from .summaries import SummaryRepository
from .tasks import TaskRepository

__all__ = [
    "CaseRepository",
    "MetaRepository",
    "SummaryRepository",
    "TaskRepository",
]
