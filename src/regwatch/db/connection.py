"""连接工厂。

统一负责：

- WAL 模式 + ``busy_timeout``，让「后台任务写 + 网页端读」并发安全；
- 外键约束开启，摘要与正文随案例级联删除；
- **每线程一个连接**（``threading.local``），避免跨线程使用 sqlite3 连接；
- 首次连接自动执行结构迁移（:func:`regwatch.db.migrations.ensure_schema`）。

内存库（``:memory:``）例外：所有线程共用一个连接，专供测试使用。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .migrations import ensure_schema

__all__ = ["Database", "DatabaseError"]

#: 默认等锁时间（毫秒）
DEFAULT_BUSY_TIMEOUT_MS = 30_000


class DatabaseError(RuntimeError):
    """数据库操作失败。"""


class Database:
    """一个 SQLite 库文件的访问入口（线程安全）。"""

    def __init__(
        self,
        path: Path | str,
        *,
        timeout: float = DEFAULT_BUSY_TIMEOUT_MS / 1000,
        autocommit: bool = True,
    ) -> None:
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        self._timeout = timeout
        self._autocommit = autocommit
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None
        self._shared_lock = threading.RLock()
        self._schema_ready = False

    # ── 属性 ──

    @property
    def is_memory(self) -> bool:
        return str(self.path) == ":memory:"

    def exists(self) -> bool:
        """库文件是否已存在于磁盘。"""
        return not self.is_memory and self.path.exists()

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Database(path={str(self.path)!r})"

    # ── 连接 ──

    def _new_connection(self) -> sqlite3.Connection:
        if not self.is_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            timeout=self._timeout,
            # None = 自动提交（默认）；显式事务由 Database.transaction 控制
            isolation_level=None if self._autocommit else "DEFERRED",
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        if not self.is_memory:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def connect(self) -> sqlite3.Connection:
        """返回当前线程的连接（首次调用时创建并执行结构迁移）。"""
        if self.is_memory:
            with self._shared_lock:
                if self._shared is None:
                    self._shared = self._new_connection()
                    ensure_schema(self._shared)
                return self._shared

        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            ensure_schema(conn)
            self._local.conn = conn
        return conn

    # ── 事务 ──

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """写事务上下文：``BEGIN IMMEDIATE`` + 提交 / 回滚。"""
        conn = self.connect()
        if self.is_memory:
            with self._shared_lock:
                yield from self._run_transaction(conn)
        else:
            yield from self._run_transaction(conn)

    def _run_transaction(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    # ── 便捷执行 ──

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, params)

    def executemany(self, sql: str, seq: list[tuple] | list[dict]) -> sqlite3.Cursor:
        return self.connect().executemany(sql, seq)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return list(self.connect().execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.connect().execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple | dict = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        return row[0] if row is not None else default

    # ── 生命周期 ──

    def close(self) -> None:
        """关闭当前线程的连接（内存库则关闭共享连接）。"""
        if self.is_memory:
            with self._shared_lock:
                if self._shared is not None:
                    self._shared.close()
                    self._shared = None
            return
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
