"""摘要仓储：结构化摘要与多值违规类型。"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from ...domain import (
    CaseQuery,
    CaseStatus,
    Dataset,
    SummaryRecord,
    canonical_violations,
)
from ..connection import Database
from ._filters import build_where

logger = logging.getLogger("regwatch.db.summaries")

__all__ = ["SummaryRepository"]

_SUMMARY_COLUMNS: tuple[str, ...] = (
    "dataset",
    "case_id",
    "entity_type",
    "punished_entity",
    "violation_type",
    "punishment",
    "punishment_date",
    "involved_fund",
    "violation_summary",
    "legal_basis",
    "penalty_amount",
    "market_ban",
    "extract_success",
    "error",
    "extract_time",
    "llm_provider",
    "llm_model",
)

_INSERT_SQL = f"""
INSERT INTO summaries ({", ".join(_SUMMARY_COLUMNS)})
VALUES ({", ".join("?" for _ in _SUMMARY_COLUMNS)})
ON CONFLICT(dataset, case_id) DO UPDATE SET
    {", ".join(f"{name} = excluded.{name}" for name in _SUMMARY_COLUMNS if name not in ("dataset", "case_id"))}
"""


class SummaryRepository:
    """摘要与违规类型关联表的持久化。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── 写入 ──

    def _values(self, summary: SummaryRecord) -> tuple[Any, ...]:
        return (
            summary.dataset.value,
            summary.case_id,
            summary.entity_type,
            summary.punished_entity,
            summary.violation_type,
            summary.punishment,
            summary.punishment_date,
            summary.involved_fund,
            summary.violation_summary,
            summary.legal_basis,
            summary.penalty_amount,
            summary.market_ban,
            1 if summary.extract_success else 0,
            summary.error,
            summary.extract_time,
            summary.llm_provider,
            summary.llm_model,
        )

    def upsert(
        self,
        summary: SummaryRecord,
        *,
        status: CaseStatus | str | None = None,
        note: str = "",
    ) -> None:
        """写入摘要，同步重建违规类型关联表，并可选更新案例状态。

        关联表存 **canonical 违规类型**（``normalize_violations`` 归一后），
        因此筛选与统计口径跨数据集一致；``summaries.violation_type``
        仍保留模型原文，便于回溯。
        """
        if not summary.case_id:
            raise ValueError("摘要缺少 case_id")

        dataset = summary.dataset.value
        violations = canonical_violations(summary.violation_type)

        with self._db.transaction() as conn:
            conn.execute(_INSERT_SQL, self._values(summary))
            conn.execute(
                "DELETE FROM case_violations WHERE dataset = ? AND case_id = ?",
                (dataset, summary.case_id),
            )
            if violations:
                conn.executemany(
                    "INSERT INTO case_violations (dataset, case_id, violation) VALUES (?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    [(dataset, summary.case_id, item) for item in violations],
                )
            if status is not None:
                value = status.value if isinstance(status, CaseStatus) else str(status)
                conn.execute(
                    "UPDATE cases SET status = ?, status_note = ?, updated_at = datetime('now') "
                    "WHERE dataset = ? AND case_id = ?",
                    (value, note, dataset, summary.case_id),
                )

    def upsert_many(
        self,
        summaries: Iterable[SummaryRecord],
        *,
        status: CaseStatus | str | None = None,
        batch_size: int = 500,
    ) -> int:
        """批量写入摘要，返回条数。"""
        rows: list[tuple[Any, ...]] = []
        links: list[tuple[str, str, str]] = []
        status_rows: list[tuple[str, str, str]] = []
        count = 0

        with self._db.transaction() as conn:
            for summary in summaries:
                if not summary.case_id:
                    continue
                dataset = summary.dataset.value
                rows.append(self._values(summary))
                links.extend(
                    (dataset, summary.case_id, item)
                    for item in canonical_violations(summary.violation_type)
                )
                if status is not None:
                    value = status.value if isinstance(status, CaseStatus) else str(status)
                    status_rows.append((value, dataset, summary.case_id))
                count += 1

                if len(rows) >= batch_size:
                    conn.executemany(_INSERT_SQL, rows)
                    rows.clear()

            if rows:
                conn.executemany(_INSERT_SQL, rows)

            if links:
                keys = sorted({(item[0], item[1]) for item in links})
                conn.executemany(
                    "DELETE FROM case_violations WHERE dataset = ? AND case_id = ?", keys
                )
                conn.executemany(
                    "INSERT INTO case_violations (dataset, case_id, violation) VALUES (?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    links,
                )

            if status_rows:
                conn.executemany(
                    "UPDATE cases SET status = ?, status_note = '', "
                    "updated_at = datetime('now') WHERE dataset = ? AND case_id = ?",
                    status_rows,
                )
        return count

    def rebuild_violations(self) -> int:
        """按当前分类体系从 ``summaries.violation_type`` 重建违规类型关联表。

        用于分类体系升级（合并同义类型、补充别名）后回填历史数据，
        **不重新调用模型**；返回重建的摘要条数。
        """
        rows = self._db.query("SELECT dataset, case_id, violation_type FROM summaries")
        links: list[tuple[str, str, str]] = []
        for row in rows:
            dataset = str(row["dataset"])
            case_id = str(row["case_id"])
            links.extend(
                (dataset, case_id, item)
                for item in canonical_violations(str(row["violation_type"] or ""))
            )
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM case_violations")
            if links:
                conn.executemany(
                    "INSERT INTO case_violations (dataset, case_id, violation) VALUES (?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    links,
                )
        return len(rows)

    def delete(self, dataset: Dataset | str, case_id: str) -> bool:
        cursor = self._db.execute(
            "DELETE FROM summaries WHERE dataset = ? AND case_id = ?",
            (_dataset_value(dataset), case_id),
        )
        self._db.execute(
            "DELETE FROM case_violations WHERE dataset = ? AND case_id = ?",
            (_dataset_value(dataset), case_id),
        )
        return bool(cursor.rowcount)

    def set_punishment_date(
        self,
        dataset: Dataset | str,
        case_id: str,
        punishment_date: str,
        *,
        only_if_empty: bool = True,
    ) -> bool:
        """回填处分日期；默认只填空值，不覆盖模型结果。"""
        value = _dataset_value(dataset)
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE summaries SET punishment_date = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (punishment_date IS NULL OR TRIM(punishment_date) = '')",
                (punishment_date, value, case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE summaries SET punishment_date = ? WHERE dataset = ? AND case_id = ?",
                (punishment_date, value, case_id),
            )
        return bool(cursor.rowcount)

    def set_violation_type(self, dataset: Dataset | str, case_id: str, violation_type: str) -> bool:
        """人工/规则修正违规类型原文，并重建该案例的关联表。"""
        value = _dataset_value(dataset)
        summary = self.get(value, case_id)
        if summary is None:
            return False
        summary.violation_type = violation_type
        self.upsert(summary)
        return True

    def set_punished_entity(
        self,
        dataset: Dataset | str,
        case_id: str,
        punished_entity: str,
        *,
        only_if_empty: bool = True,
    ) -> bool:
        """回填摘要侧当事人；默认只填空。"""
        value = _dataset_value(dataset)
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE summaries SET punished_entity = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (punished_entity IS NULL OR TRIM(punished_entity) = '')",
                (punished_entity, value, case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE summaries SET punished_entity = ? WHERE dataset = ? AND case_id = ?",
                (punished_entity, value, case_id),
            )
        return bool(cursor.rowcount)

    def set_entity_type(
        self,
        dataset: Dataset | str,
        case_id: str,
        entity_type: str,
        *,
        only_if_empty: bool = False,
    ) -> bool:
        """回填/规范主体类型（机构 / 个人 / 机构、个人）。"""
        value = _dataset_value(dataset)
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE summaries SET entity_type = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (entity_type IS NULL OR TRIM(entity_type) = '')",
                (entity_type, value, case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE summaries SET entity_type = ? WHERE dataset = ? AND case_id = ?",
                (entity_type, value, case_id),
            )
        return bool(cursor.rowcount)

    def set_punishment(
        self,
        dataset: Dataset | str,
        case_id: str,
        punishment: str,
        *,
        only_if_empty: bool = True,
    ) -> bool:
        """回填处罚措施；默认只填空。"""
        value = _dataset_value(dataset)
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE summaries SET punishment = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (punishment IS NULL OR TRIM(punishment) = '')",
                (punishment, value, case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE summaries SET punishment = ? WHERE dataset = ? AND case_id = ?",
                (punishment, value, case_id),
            )
        return bool(cursor.rowcount)

    def list_for_field_fill(
        self,
        *,
        field: str,
        dataset: Dataset | str | None = None,
        extract_success: bool = True,
    ) -> list[dict[str, Any]]:
        """列出摘要中指定字段为空的案例（供确定性回填）。"""
        if field not in _SUMMARY_COLUMNS:
            raise ValueError(f"未知摘要字段: {field}")
        sql = (
            f"SELECT s.dataset, s.case_id, s.{field} AS field_value, c.title, c.punished_entity, "
            f"c.punished_entities, c.document_number, c.date, c.status "
            f"FROM summaries s JOIN cases c ON c.dataset = s.dataset AND c.case_id = s.case_id "
            f"WHERE TRIM(COALESCE(s.{field}, '')) = ''"
        )
        params: list[Any] = []
        if extract_success:
            sql += " AND s.extract_success = 1"
        if dataset is not None:
            sql += " AND s.dataset = ?"
            params.append(_dataset_value(dataset))
        sql += " ORDER BY s.dataset, s.case_id"
        return [dict(row) for row in self._db.query(sql, tuple(params))]

    # ── 读取 ──

    def get(self, dataset: Dataset | str, case_id: str) -> SummaryRecord | None:
        row = self._db.query_one(
            f"SELECT {', '.join(_SUMMARY_COLUMNS)} FROM summaries WHERE dataset = ? AND case_id = ?",
            (_dataset_value(dataset), case_id),
        )
        return _summary_from_row(row) if row is not None else None

    def get_many(self, dataset: Dataset | str, case_ids: Sequence[str]) -> dict[str, SummaryRecord]:
        if not case_ids:
            return {}
        placeholders = ", ".join("?" for _ in case_ids)
        sql = (
            f"SELECT {', '.join(_SUMMARY_COLUMNS)} FROM summaries "
            f"WHERE dataset = ? AND case_id IN ({placeholders})"
        )
        params: tuple[Any, ...] = (_dataset_value(dataset), *case_ids)
        return {str(row["case_id"]): _summary_from_row(row) for row in self._db.query(sql, params)}

    def exists(self, dataset: Dataset | str, case_id: str) -> bool:
        return (
            self._db.scalar(
                "SELECT 1 FROM summaries WHERE dataset = ? AND case_id = ?",
                (_dataset_value(dataset), case_id),
            )
            is not None
        )

    def violations_of(self, dataset: Dataset | str, case_id: str) -> tuple[str, ...]:
        rows = self._db.query(
            "SELECT violation FROM case_violations WHERE dataset = ? AND case_id = ? "
            "ORDER BY violation",
            (_dataset_value(dataset), case_id),
        )
        return tuple(str(row["violation"]) for row in rows)

    def count(self, dataset: Dataset | str | None = None) -> int:
        if dataset is None:
            return int(self._db.scalar("SELECT COUNT(*) FROM summaries", default=0) or 0)
        return int(
            self._db.scalar(
                "SELECT COUNT(*) FROM summaries WHERE dataset = ?",
                (_dataset_value(dataset),),
                default=0,
            )
            or 0
        )

    # ── 统计 ──

    def violation_options(self, limit: int = 0) -> list[str]:
        """全部出现过的违规类型（canonical，供筛选下拉）。"""
        sql = "SELECT violation, COUNT(*) AS total FROM case_violations GROUP BY violation ORDER BY total DESC, violation ASC"
        if limit > 0:
            sql += " LIMIT ?"
            rows = self._db.query(sql, (limit,))
        else:
            rows = self._db.query(sql)
        return [str(row["violation"]) for row in rows]

    def violation_distribution(self, query: CaseQuery) -> list[tuple[str, int]]:
        """在给定筛选范围内统计违规类型分布（canonical，一个案例可计入多类）。"""
        where, params = build_where(query)
        sql = (
            "SELECT v.violation AS value, COUNT(*) AS total "
            "FROM case_violations v "
            "JOIN cases c ON c.dataset = v.dataset AND c.case_id = v.case_id "
            "LEFT JOIN summaries s ON s.dataset = c.dataset AND s.case_id = c.case_id "
            f"WHERE {where} GROUP BY v.violation ORDER BY total DESC, value ASC"
        )
        return [
            (str(row["value"]), int(row["total"])) for row in self._db.query(sql, tuple(params))
        ]


def _dataset_value(dataset: Dataset | str) -> str:
    return dataset.value if isinstance(dataset, Dataset) else str(dataset)


def _summary_from_row(row: Any) -> SummaryRecord:
    return SummaryRecord(
        dataset=str(row["dataset"]),
        case_id=str(row["case_id"]),
        entity_type=str(row["entity_type"] or ""),
        punished_entity=str(row["punished_entity"] or ""),
        violation_type=str(row["violation_type"] or ""),
        punishment=str(row["punishment"] or ""),
        punishment_date=str(row["punishment_date"] or ""),
        involved_fund=str(row["involved_fund"] or ""),
        violation_summary=str(row["violation_summary"] or ""),
        legal_basis=str(row["legal_basis"] or ""),
        penalty_amount=str(row["penalty_amount"] or ""),
        market_ban=str(row["market_ban"] or ""),
        extract_success=bool(row["extract_success"]),
        error=str(row["error"] or ""),
        extract_time=str(row["extract_time"] or ""),
        llm_provider=str(row["llm_provider"] or ""),
        llm_model=str(row["llm_model"] or ""),
    )
