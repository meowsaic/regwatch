"""数据层：SQLite 连接 + 四个仓储。

典型用法::

    from regwatch.db import DataStore

    store = DataStore.open("data/regwatch.db")
    store.cases.upsert(case)
    rows = store.cases.search(CaseQuery(datasets=(Dataset.AMAC,)))
"""

from __future__ import annotations

import logging
from pathlib import Path

from .connection import Database, DatabaseError
from .migrations import SCHEMA_VERSION, ensure_schema
from .repositories import CaseRepository, MetaRepository, SummaryRepository, TaskRepository

logger = logging.getLogger("regwatch.db")

__all__ = [
    "SCHEMA_VERSION",
    "CaseRepository",
    "DataStore",
    "Database",
    "DatabaseError",
    "MetaRepository",
    "SummaryRepository",
    "TaskRepository",
    "ensure_schema",
]


class DataStore:
    """一组仓储的门面：持有连接并暴露四个仓储。"""

    def __init__(self, database: Database) -> None:
        self.db = database
        self.cases = CaseRepository(database)
        self.summaries = SummaryRepository(database)
        self.tasks = TaskRepository(database)
        self.meta = MetaRepository(database)

    # ── 构造 ──

    @classmethod
    def open(cls, path: Path | str, **kwargs: object) -> DataStore:
        """打开（必要时创建）一个库文件。"""
        return cls(Database(path, **kwargs))  # type: ignore[arg-type]

    @classmethod
    def in_memory(cls) -> DataStore:
        """打开内存库（测试用）。"""
        return cls(Database(":memory:"))

    # ── 便捷属性 ──

    @property
    def path(self) -> Path:
        return self.db.path

    def revision(self) -> int:
        """数据版本号，供上层做缓存键。"""
        return self.meta.revision()

    def touch(self) -> int:
        """批量写入后调用：自增数据版本号。"""
        return self.meta.bump_revision()

    def close(self) -> None:
        self.db.close()
