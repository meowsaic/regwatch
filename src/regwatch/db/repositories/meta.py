"""键值仓储：数据版本、机构类型缓存与抓取断点。

``revision`` 是一个单调递增的计数器，任何批量写入后都会自增；
网页端用它做缓存键，替代旧版「扫描目录 mtime」的做法——更轻也更可靠。
"""

from __future__ import annotations

import logging

from ...clock import now_iso
from ..connection import Database

logger = logging.getLogger("regwatch.db.meta")

__all__ = ["REVISION_KEY", "MetaRepository"]


class MetaRepository:
    """``meta`` / ``org_type_cache`` / ``fetch_state`` 三张表的访问封装。"""

    REVISION_KEY = "revision"

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── 通用键值 ──

    def get(self, key: str, default: str = "") -> str:
        row = self._db.query_one("SELECT value FROM meta WHERE key = ?", (key,))
        return str(row["value"]) if row is not None else default

    def set(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now_iso()),
        )

    # ── 数据版本 ──

    def revision(self) -> int:
        raw = self.get(self.REVISION_KEY, "0")
        try:
            return int(raw)
        except ValueError:
            return 0

    def bump_revision(self) -> int:
        """数据变更后自增版本号，返回新值。"""
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (self.REVISION_KEY,)
            ).fetchone()
            current = 0
            if row is not None:
                try:
                    current = int(row["value"])
                except (TypeError, ValueError):
                    current = 0
            nxt = current + 1
            conn.execute(
                "INSERT INTO meta (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (self.REVISION_KEY, str(nxt), now_iso()),
            )
            return nxt

    # ── 机构登记类型缓存 ──

    def org_type(self, name: str) -> str:
        row = self._db.query_one("SELECT org_type FROM org_type_cache WHERE name = ?", (name,))
        return str(row["org_type"]) if row is not None else ""

    def set_org_type(self, name: str, org_type: str, source: str = "") -> None:
        self._db.execute(
            "INSERT INTO org_type_cache (name, org_type, source, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET org_type = excluded.org_type, "
            "source = excluded.source, updated_at = excluded.updated_at",
            (name, org_type, source, now_iso()),
        )

    def org_types(self) -> dict[str, str]:
        rows = self._db.query("SELECT name, org_type FROM org_type_cache ORDER BY name")
        return {str(row["name"]): str(row["org_type"]) for row in rows}

    # ── 抓取断点 ──

    def fetch_state(self, source: str, scope: str = "", key: str = "") -> str:
        row = self._db.query_one(
            "SELECT value FROM fetch_state WHERE source = ? AND scope = ? AND key = ?",
            (source, scope, key),
        )
        return str(row["value"]) if row is not None else ""

    def set_fetch_state(self, source: str, scope: str, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO fetch_state (source, scope, key, value, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(source, scope, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (source, scope, key, value, now_iso()),
        )

    def fetch_states(self, source: str, scope: str = "") -> dict[str, str]:
        rows = self._db.query(
            "SELECT key, value FROM fetch_state WHERE source = ? AND scope = ? ORDER BY key",
            (source, scope),
        )
        return {str(row["key"]): str(row["value"]) for row in rows}


REVISION_KEY = MetaRepository.REVISION_KEY
