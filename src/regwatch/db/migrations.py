"""基于 ``PRAGMA user_version`` 的顺序迁移器。

每次进程首次连库时调用 :func:`ensure_schema`：

- 版本已是最新 → 直接返回；
- 版本落后 → 依次执行缺失的迁移步骤（全部包在一个事务里，失败整体回滚）；
- 新库 → 执行 ``schema.sql`` 建表并写入当前版本号。
"""

from __future__ import annotations

import logging
import sqlite3
from importlib.resources import files

logger = logging.getLogger("regwatch.db")

#: 当前 schema 版本；新增表或列时 +1 并把变更写进 :data:`MIGRATIONS`
SCHEMA_VERSION = 1

__all__ = ["MIGRATIONS", "SCHEMA_VERSION", "ensure_schema", "load_schema_sql"]


def load_schema_sql() -> str:
    """读取随包发布的 ``schema.sql``。"""
    return (files(__package__) / "schema.sql").read_text(encoding="utf-8")


def split_statements(sql: str) -> list[str]:
    """把 SQL 脚本拆成单条语句。

    ``sqlite3.executescript`` 会先隐式提交当前事务，无法用于「整库升级」这种
    需要原子性的场景，因此这里按行拼接、遇到行尾分号断句，再逐条执行。
    """
    statements: list[str] = []
    buffer: list[str] = []
    for raw_line in sql.splitlines():
        # 去掉行尾注释：否则拼接成单行后，注释会把后续内容一起吞掉
        line = raw_line.split("--", 1)[0].strip()
        if not line:
            continue
        buffer.append(line)
        if line.endswith(";"):
            statements.append(" ".join(buffer).rstrip(";"))
            buffer.clear()
    if buffer:
        statements.append(" ".join(buffer))
    return [stmt for stmt in statements if stmt]


def _create_initial_schema(conn: sqlite3.Connection) -> None:
    for statement in split_statements(load_schema_sql()):
        conn.execute(statement)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES ('revision', '0', '')"
    )


#: 迁移步骤：索引 ``i`` 处的函数把库从 ``user_version = i`` 升到 ``i + 1``
MIGRATIONS: list = [_create_initial_schema]


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def ensure_schema(conn: sqlite3.Connection) -> int:
    """把库结构升级到 :data:`SCHEMA_VERSION`，返回升级前的版本号。"""
    version = current_version(conn)
    if version >= SCHEMA_VERSION:
        return version

    conn.execute("BEGIN IMMEDIATE")
    try:
        for step in MIGRATIONS[version:]:
            step(conn)
        # PRAGMA 不能带参数，版本常量来自本模块且为整数，拼接安全
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:  # pragma: no cover - 事务已被脚本提交
            pass
        logger.exception("数据库结构升级失败（从版本 %s 到 %s）", version, SCHEMA_VERSION)
        raise

    logger.debug("数据库结构已就绪：版本 %s → %s", version, SCHEMA_VERSION)
    return version
