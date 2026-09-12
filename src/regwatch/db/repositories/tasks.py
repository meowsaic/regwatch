"""任务仓储：后台任务状态与日志持久化。

进程重启后任务历史仍在，网页端「任务中心」可回看历史记录，
不再像旧版那样只存在于内存。
"""

from __future__ import annotations

import builtins
import json
import logging
from typing import Any

from ...domain import JobKind, JobStatus, TaskLogLine, TaskRecord
from ..connection import Database

logger = logging.getLogger("regwatch.db.tasks")

__all__ = ["TaskRepository"]

_MAX_KEEP = 100


class TaskRepository:
    """任务与任务日志的持久化。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── 写入 ──

    def save(self, record: TaskRecord) -> None:
        """新增或整体覆盖一条任务记录。"""
        self._db.execute(
            "INSERT INTO tasks (id, kind, title, status, created_at, started_at, finished_at, "
            "params, processed, total, message, error, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "kind = excluded.kind, title = excluded.title, status = excluded.status, "
            "started_at = excluded.started_at, finished_at = excluded.finished_at, "
            "params = excluded.params, processed = excluded.processed, total = excluded.total, "
            "message = excluded.message, error = excluded.error, result = excluded.result",
            (
                record.id,
                record.kind.value,
                record.title,
                record.status.value,
                record.created_at,
                record.started_at,
                record.finished_at,
                json.dumps(record.params, ensure_ascii=False),
                record.processed,
                record.total,
                record.message,
                record.error,
                json.dumps(record.result, ensure_ascii=False),
            ),
        )

    def update_progress(
        self,
        task_id: str,
        *,
        processed: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        """只更新进度相关字段，避免覆盖并发写入的其它字段。"""
        fields: list[str] = []
        params: list[Any] = []
        if processed is not None:
            fields.append("processed = ?")
            params.append(int(processed))
        if total is not None:
            fields.append("total = ?")
            params.append(int(total))
        if message is not None:
            fields.append("message = ?")
            params.append(message)
        if not fields:
            return
        params.append(task_id)
        self._db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", tuple(params))

    def finish(
        self,
        task_id: str,
        status: JobStatus,
        *,
        error: str = "",
        message: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        """标记任务结束。"""
        from ...clock import now_iso

        payload = result if result is not None else None
        if payload is None:
            self._db.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, error = ?, message = ? WHERE id = ?",
                (status.value, now_iso(), error, message, task_id),
            )
        else:
            self._db.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, error = ?, message = ?, result = ? "
                "WHERE id = ?",
                (
                    status.value,
                    now_iso(),
                    error,
                    message,
                    json.dumps(payload, ensure_ascii=False),
                    task_id,
                ),
            )

    # ── 日志 ──

    def append_log(self, line: TaskLogLine) -> None:
        """追加一行任务日志。"""
        self._db.execute(
            "INSERT INTO task_logs (task_id, seq, level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (line.task_id, line.seq, line.level, line.message, line.created_at),
        )

    def logs(self, task_id: str, start: int = 0) -> list[TaskLogLine]:
        rows = self._db.query(
            "SELECT task_id, seq, level, message, created_at FROM task_logs "
            "WHERE task_id = ? AND seq >= ? ORDER BY seq",
            (task_id, start),
        )
        return [
            TaskLogLine(
                task_id=str(row["task_id"]),
                seq=int(row["seq"]),
                level=str(row["level"]),
                message=str(row["message"]),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]

    def log_count(self, task_id: str) -> int:
        return int(
            self._db.scalar(
                "SELECT COUNT(*) FROM task_logs WHERE task_id = ?", (task_id,), default=0
            )
            or 0
        )

    # ── 读取 ──

    def get(self, task_id: str) -> TaskRecord | None:
        row = self._db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _task_from_row(row) if row is not None else None

    # 注意：返回注解用 typing.List，避免与该方法名遮蔽内建 list
    def list(  # type: ignore[valid-type]
        self,
        *,
        kinds: tuple[JobKind | str, ...] = (),
        status: JobStatus | str | None = None,
        limit: int = 50,
    ) -> builtins.list[TaskRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if kinds:
            placeholders = ", ".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(k.value if isinstance(k, JobKind) else str(k) for k in kinds)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value if isinstance(status, JobStatus) else str(status))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY created_at DESC, rowid DESC"
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        return [_task_from_row(row) for row in self._db.query(sql, tuple(params))]

    def running_ids(self) -> builtins.list[str]:
        """仍在运行中的任务 ID（启动恢复用）。"""
        rows = self._db.query("SELECT id FROM tasks WHERE status IN ('pending', 'running')")
        return [str(row["id"]) for row in rows]

    def delete(self, task_id: str) -> bool:
        cursor = self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._db.execute("DELETE FROM task_logs WHERE task_id = ?", (task_id,))
        return bool(cursor.rowcount)

    def prune(self, keep: int = _MAX_KEEP) -> int:
        """清理已结束任务的超量历史（含日志），返回删除条数。"""
        stale = self._db.query(
            "SELECT id FROM tasks WHERE status IN ('success', 'failed', 'cancelled') "
            "ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?",
            (max(0, keep),),
        )
        ids = [str(row["id"]) for row in stale]
        with self._db.transaction() as conn:
            for task_id in ids:
                conn.execute("DELETE FROM task_logs WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return len(ids)


def _task_from_row(row: Any) -> TaskRecord:
    return TaskRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        title=str(row["title"] or ""),
        status=str(row["status"] or JobStatus.PENDING.value),
        created_at=str(row["created_at"] or ""),
        started_at=str(row["started_at"] or ""),
        finished_at=str(row["finished_at"] or ""),
        params=_load_json(row["params"]),
        processed=int(row["processed"] or 0),
        total=int(row["total"] or 0),
        message=str(row["message"] or ""),
        error=str(row["error"] or ""),
        result=_load_json(row["result"]),
    )


def _load_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}
