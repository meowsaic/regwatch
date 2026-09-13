"""案例仓储：案例、正文与统计聚合。

所有落盘都经过这里，采集层不再直接写文件。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from ...clock import now_iso
from ...domain import CaseQuery, CaseRecord, CaseRow, CaseStatus, Dataset
from ..connection import Database
from ._filters import GROUP_COLUMNS, build_order_by, build_where

logger = logging.getLogger("regwatch.db.cases")

__all__ = ["CaseRepository"]

_CASE_COLUMNS: tuple[str, ...] = (
    "dataset",
    "case_id",
    "source_url",
    "title",
    "date",
    "status",
    "status_note",
    "fetch_time",
    "error",
    "pdf_url",
    "category",
    "org_type",
    "punished_entity",
    "source_type",
    "ocr_success",
    "case_type",
    "bureau",
    "document_number",
    "punished_entities",
    "is_fund_related",
    "fund_evidence",
    "doc_url",
)

_INSERT_SQL = f"""
INSERT INTO cases ({", ".join(_CASE_COLUMNS)}, updated_at)
VALUES ({", ".join("?" for _ in _CASE_COLUMNS)}, ?)
ON CONFLICT(dataset, case_id) DO UPDATE SET
    {", ".join(f"{name} = excluded.{name}" for name in _CASE_COLUMNS if name not in ("dataset", "case_id"))},
    updated_at = excluded.updated_at,
    -- 抓取不会把已完成的案例退回待处理；failed 允许重试回到 pending
    status = CASE
        WHEN excluded.status = 'pending' AND cases.status IN ('done', 'skipped')
        THEN cases.status
        ELSE excluded.status
    END
"""

_SELECT_ROW = """
SELECT
    c.dataset, c.case_id, c.source_url, c.title, c.date, c.status, c.status_note,
    c.fetch_time, c.error, c.pdf_url, c.bureau, c.case_type, c.category,
    c.org_type, c.document_number, c.punished_entities, c.punished_entity,
    c.is_fund_related, c.fund_evidence, c.doc_url,
    COALESCE(s.entity_type, '')          AS entity_type,
    COALESCE(NULLIF(s.punished_entity, ''), c.punished_entity, '') AS summary_punished_entity,
    COALESCE(s.violation_type, '')       AS violation_type,
    COALESCE(s.punishment, '')           AS punishment,
    COALESCE(s.punishment_date, '')      AS punishment_date,
    COALESCE(s.involved_fund, '')        AS involved_fund,
    COALESCE(s.violation_summary, '')    AS violation_summary,
    COALESCE(s.legal_basis, '')          AS legal_basis,
    COALESCE(s.penalty_amount, '')       AS penalty_amount,
    COALESCE(s.market_ban, '')           AS market_ban,
    COALESCE(s.extract_time, '')         AS extract_time,
    EXISTS(SELECT 1 FROM case_bodies b
           WHERE b.dataset = c.dataset AND b.case_id = c.case_id) AS has_body
FROM cases c
LEFT JOIN summaries s ON s.dataset = c.dataset AND s.case_id = c.case_id
"""


class CaseRepository:
    """案例与正文的持久化。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── 写入 ──

    def _values(self, case: CaseRecord) -> tuple[Any, ...]:
        return (
            case.dataset.value,
            case.case_id,
            case.source_url,
            case.title,
            case.date,
            case.status.value,
            case.status_note,
            case.fetch_time,
            case.error,
            case.pdf_url,
            case.category.value,
            case.org_type,
            case.punished_entity,
            case.source_type,
            1 if case.ocr_success else 0,
            case.case_type.value,
            case.bureau,
            case.document_number,
            case.punished_entities,
            None if case.is_fund_related is None else (1 if case.is_fund_related else 0),
            case.fund_evidence,
            case.doc_url,
            now_iso(),
        )

    def upsert(self, case: CaseRecord) -> None:
        """写入案例；``raw_text`` 非空时同步写入正文表。"""
        if not case.case_id:
            raise ValueError("案例缺少 case_id")
        with self._db.transaction() as conn:
            conn.execute(_INSERT_SQL, self._values(case))
            if case.raw_text:
                conn.execute(
                    "INSERT INTO case_bodies (dataset, case_id, raw_text) VALUES (?, ?, ?) "
                    "ON CONFLICT(dataset, case_id) DO UPDATE SET raw_text = excluded.raw_text",
                    (case.dataset.value, case.case_id, case.raw_text),
                )

    def upsert_many(self, cases: Iterable[CaseRecord], *, batch_size: int = 500) -> int:
        """批量写入，返回写入条数。"""
        count = 0
        batch: list[tuple[Any, ...]] = []
        bodies: list[tuple[str, str, str]] = []

        with self._db.transaction() as conn:
            for case in cases:
                if not case.case_id:
                    continue
                batch.append(self._values(case))
                if case.raw_text:
                    bodies.append((case.dataset.value, case.case_id, case.raw_text))
                count += 1
                if len(batch) >= batch_size:
                    conn.executemany(_INSERT_SQL, batch)
                    if bodies:
                        conn.executemany(
                            "INSERT INTO case_bodies (dataset, case_id, raw_text) VALUES (?, ?, ?) "
                            "ON CONFLICT(dataset, case_id) DO UPDATE SET raw_text = excluded.raw_text",
                            bodies,
                        )
                    batch.clear()
                    bodies.clear()
            if batch:
                conn.executemany(_INSERT_SQL, batch)
            if bodies:
                conn.executemany(
                    "INSERT INTO case_bodies (dataset, case_id, raw_text) VALUES (?, ?, ?) "
                    "ON CONFLICT(dataset, case_id) DO UPDATE SET raw_text = excluded.raw_text",
                    bodies,
                )
        return count

    def set_body(self, dataset: Dataset | str, case_id: str, raw_text: str) -> None:
        """单独写入 / 覆盖正文。"""
        self._db.execute(
            "INSERT INTO case_bodies (dataset, case_id, raw_text) VALUES (?, ?, ?) "
            "ON CONFLICT(dataset, case_id) DO UPDATE SET raw_text = excluded.raw_text",
            (_dataset_value(dataset), case_id, raw_text),
        )

    def set_status(
        self,
        dataset: Dataset | str,
        case_id: str,
        status: CaseStatus | str,
        note: str = "",
    ) -> None:
        """更新案例处理状态（摘要流程用）。"""
        value = status.value if isinstance(status, CaseStatus) else str(status)
        self._db.execute(
            "UPDATE cases SET status = ?, status_note = ?, updated_at = ? "
            "WHERE dataset = ? AND case_id = ?",
            (value, note, now_iso(), _dataset_value(dataset), case_id),
        )

    def set_punished_entity(
        self, dataset: Dataset | str, case_id: str, entity: str, *, only_if_empty: bool = True
    ) -> bool:
        """回填当事人；``only_if_empty`` 时不覆盖已有非空值。返回是否写入。"""
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE cases SET punished_entity = ?, updated_at = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (punished_entity IS NULL OR TRIM(punished_entity) = '')",
                (entity, now_iso(), _dataset_value(dataset), case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE cases SET punished_entity = ?, updated_at = ? "
                "WHERE dataset = ? AND case_id = ?",
                (entity, now_iso(), _dataset_value(dataset), case_id),
            )
        return bool(cursor.rowcount)

    def set_punished_entities(
        self, dataset: Dataset | str, case_id: str, entities: str, *, only_if_empty: bool = True
    ) -> bool:
        """回填 CSRC 受处罚主体（``punished_entities``）。返回是否写入。"""
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE cases SET punished_entities = ?, updated_at = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (punished_entities IS NULL OR TRIM(punished_entities) = '')",
                (entities, now_iso(), _dataset_value(dataset), case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE cases SET punished_entities = ?, updated_at = ? "
                "WHERE dataset = ? AND case_id = ?",
                (entities, now_iso(), _dataset_value(dataset), case_id),
            )
        return bool(cursor.rowcount)

    def set_document_number(
        self, dataset: Dataset | str, case_id: str, number: str, *, only_if_empty: bool = True
    ) -> bool:
        """回填文书号；``only_if_empty`` 时不覆盖已有非空值。返回是否写入。"""
        if only_if_empty:
            cursor = self._db.execute(
                "UPDATE cases SET document_number = ?, updated_at = ? "
                "WHERE dataset = ? AND case_id = ? "
                "AND (document_number IS NULL OR TRIM(document_number) = '')",
                (number, now_iso(), _dataset_value(dataset), case_id),
            )
        else:
            cursor = self._db.execute(
                "UPDATE cases SET document_number = ?, updated_at = ? "
                "WHERE dataset = ? AND case_id = ?",
                (number, now_iso(), _dataset_value(dataset), case_id),
            )
        return bool(cursor.rowcount)

    def ids_missing_body(self, dataset: Dataset | str) -> list[str]:
        """列出没有任何正文记录的案例 ID（抓取失败 / 中断，可安全重试）。

        抓取流程用它区分「已成功入库」与「抓过但没拿到正文」：
        前者跳过，后者允许在下次抓取时重试，避免失败案例永久卡死。
        """
        rows = self._db.query(
            "SELECT c.case_id FROM cases c "
            "LEFT JOIN case_bodies b ON b.dataset = c.dataset AND b.case_id = c.case_id "
            "WHERE c.dataset = ? AND b.case_id IS NULL ORDER BY c.case_id",
            (_dataset_value(dataset),),
        )
        return [str(row["case_id"]) for row in rows]

    def list_short_bodies(self, *, max_length: int = 200) -> list[tuple[str, str, int]]:
        """正文过短的案例（可能是抓取/解析失败）。返回 (dataset, case_id, length)。"""
        rows = self._db.query(
            "SELECT b.dataset, b.case_id, LENGTH(b.raw_text) AS n "
            "FROM case_bodies b JOIN cases c ON c.dataset = b.dataset AND c.case_id = b.case_id "
            "WHERE LENGTH(b.raw_text) < ? ORDER BY n ASC, b.dataset, b.case_id",
            (max_length,),
        )
        return [(str(row["dataset"]), str(row["case_id"]), int(row["n"] or 0)) for row in rows]

    def delete(self, dataset: Dataset | str, case_id: str) -> bool:
        """删除案例（正文与摘要随外键级联）。"""
        cursor = self._db.execute(
            "DELETE FROM cases WHERE dataset = ? AND case_id = ?",
            (_dataset_value(dataset), case_id),
        )
        return bool(cursor.rowcount)

    # ── 读取 ──

    def get(self, dataset: Dataset | str, case_id: str) -> CaseRecord | None:
        """读取案例（不含正文）。"""
        row = self._db.query_one(
            f"SELECT {', '.join(_CASE_COLUMNS)} FROM cases WHERE dataset = ? AND case_id = ?",
            (_dataset_value(dataset), case_id),
        )
        return _case_from_row(row) if row is not None else None

    def get_body(self, dataset: Dataset | str, case_id: str) -> str:
        """读取案例正文，不存在时返回空串。"""
        row = self._db.query_one(
            "SELECT raw_text FROM case_bodies WHERE dataset = ? AND case_id = ?",
            (_dataset_value(dataset), case_id),
        )
        return str(row["raw_text"]) if row is not None else ""

    def get_with_body(self, dataset: Dataset | str, case_id: str) -> CaseRecord | None:
        """读取案例并附带正文。"""
        case = self.get(dataset, case_id)
        if case is None:
            return None
        case.raw_text = self.get_body(dataset, case_id)
        return case

    def ids(
        self,
        dataset: Dataset | str,
        statuses: Sequence[CaseStatus | str] | None = None,
    ) -> list[str]:
        """列出案例 ID（可按状态过滤），按 ID 排序保证顺序稳定。"""
        sql = "SELECT case_id FROM cases WHERE dataset = ?"
        params: list[Any] = [_dataset_value(dataset)]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(s.value if isinstance(s, CaseStatus) else str(s) for s in statuses)
        sql += " ORDER BY case_id"
        return [str(row["case_id"]) for row in self._db.query(sql, tuple(params))]

    def iter_cases(
        self,
        dataset: Dataset | str,
        statuses: Sequence[CaseStatus | str] | None = None,
    ) -> Iterator[CaseRecord]:
        """逐个产出案例（含正文），供摘要等批处理使用。"""
        for case_id in self.ids(dataset, statuses):
            case = self.get_with_body(dataset, case_id)
            if case is not None:
                yield case

    # ── 查询与统计 ──

    def search(self, query: CaseQuery) -> list[CaseRow]:
        """按条件查询案例行（不含正文）。"""
        where, params = build_where(query)
        sql = f"{_SELECT_ROW} WHERE {where} ORDER BY {build_order_by(query)}"
        if query.limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([query.limit, max(0, query.offset)])
        return [_row_from_query(row) for row in self._db.query(sql, tuple(params))]

    def count(self, query: CaseQuery) -> int:
        """按条件统计条数（忽略分页）。"""
        where, params = build_where(query)
        sql = (
            "SELECT COUNT(*) AS total FROM cases c "
            "LEFT JOIN summaries s ON s.dataset = c.dataset AND s.case_id = c.case_id "
            f"WHERE {where}"
        )
        return int(self._db.scalar(sql, tuple(params), default=0) or 0)

    def aggregate(self, query: CaseQuery, group_by: str) -> list[tuple[str, int]]:
        """按某一维度分组计数（SQL 聚合，不载入全部行）。"""
        column = GROUP_COLUMNS.get(group_by)
        if column is None:
            raise ValueError(f"不支持的分组维度：{group_by}")
        where, params = build_where(query)
        # 按 COALESCE 后的表达式分组，避免 NULL 与空串被拆成两行同名结果
        grouped = f"COALESCE({column}, '')"
        sql = (
            f"SELECT {grouped} AS value, COUNT(*) AS total "
            "FROM cases c LEFT JOIN summaries s ON s.dataset = c.dataset AND s.case_id = c.case_id "
            f"WHERE {where} GROUP BY {grouped} ORDER BY total DESC, value ASC"
        )
        return [
            (str(row["value"]), int(row["total"])) for row in self._db.query(sql, tuple(params))
        ]

    def time_trend(self, query: CaseQuery, granularity: str = "month") -> list[dict[str, Any]]:
        """按月 / 年统计趋势。"""
        length = 7 if granularity == "month" else 4
        where, params = build_where(query)
        # length 是代码常量，必须内联：substr 占位符出现在 WHERE 之前，
        # 若 append 到 where 参数末尾，dataset IN (?, ?) 会错位吃掉 length。
        sql = (
            f"SELECT substr(c.date, 1, {length}) AS period, COUNT(*) AS count, "
            "SUM(CASE WHEN COALESCE(s.entity_type, '') = '机构' THEN 1 ELSE 0 END) AS institutions, "
            "SUM(CASE WHEN COALESCE(s.entity_type, '') = '个人' THEN 1 ELSE 0 END) AS personnel "
            "FROM cases c LEFT JOIN summaries s ON s.dataset = c.dataset AND s.case_id = c.case_id "
            f"WHERE {where} AND length(c.date) >= {length} "
            "GROUP BY period ORDER BY period"
        )
        return [
            {
                "period": str(row["period"]),
                "count": int(row["count"]),
                "institutions": int(row["institutions"]),
                "personnel": int(row["personnel"]),
            }
            for row in self._db.query(sql, tuple(params))
        ]

    def date_range(self, query: CaseQuery) -> tuple[str, str]:
        """返回筛选结果的日期上下界。"""
        where, params = build_where(query)
        sql = (
            "SELECT MIN(c.date) AS date_min, MAX(c.date) AS date_max "
            "FROM cases c LEFT JOIN summaries s ON s.dataset = c.dataset AND s.case_id = c.case_id "
            f"WHERE {where} AND c.date <> ''"
        )
        row = self._db.query_one(sql, tuple(params))
        if row is None:
            return "", ""
        return str(row["date_min"] or ""), str(row["date_max"] or "")

    def totals(self) -> dict[str, int]:
        """全库规模指标（网页总览页用）。"""
        rows = self._db.query(
            "SELECT dataset, status, COUNT(*) AS total FROM cases GROUP BY dataset, status"
        )
        result: dict[str, int] = {}
        for row in rows:
            result[f"{row['dataset']}.{row['status']}"] = int(row["total"])
        result["all"] = int(self._db.scalar("SELECT COUNT(*) FROM cases", default=0) or 0)
        result["bodies"] = int(self._db.scalar("SELECT COUNT(*) FROM case_bodies", default=0) or 0)
        return result


# ──────────────────────────── 行映射 ────────────────────────────


def _dataset_value(dataset: Dataset | str) -> str:
    return dataset.value if isinstance(dataset, Dataset) else str(dataset)


def _case_from_row(row: Any) -> CaseRecord:
    return CaseRecord(
        dataset=str(row["dataset"]),
        case_id=str(row["case_id"]),
        source_url=str(row["source_url"] or ""),
        title=str(row["title"] or ""),
        date=str(row["date"] or ""),
        status=str(row["status"] or CaseStatus.PENDING.value),
        status_note=str(row["status_note"] or ""),
        fetch_time=str(row["fetch_time"] or ""),
        error=str(row["error"] or ""),
        pdf_url=str(row["pdf_url"] or ""),
        category=str(row["category"] or ""),
        org_type=str(row["org_type"] or ""),
        punished_entity=str(row["punished_entity"] or ""),
        source_type=str(row["source_type"] or ""),
        ocr_success=bool(row["ocr_success"]),
        case_type=str(row["case_type"] or ""),
        bureau=str(row["bureau"] or ""),
        document_number=str(row["document_number"] or ""),
        punished_entities=str(row["punished_entities"] or ""),
        is_fund_related=None if row["is_fund_related"] is None else bool(row["is_fund_related"]),
        fund_evidence=str(row["fund_evidence"] or ""),
        doc_url=str(row["doc_url"] or ""),
    )


def _row_from_query(row: Any) -> CaseRow:
    return CaseRow(
        dataset=str(row["dataset"]),
        case_id=str(row["case_id"]),
        title=str(row["title"] or ""),
        date=str(row["date"] or ""),
        source_url=str(row["source_url"] or ""),
        status=str(row["status"] or CaseStatus.PENDING.value),
        status_note=str(row["status_note"] or ""),
        bureau=str(row["bureau"] or ""),
        case_type=str(row["case_type"] or ""),
        category=str(row["category"] or ""),
        entity_type=str(row["entity_type"] or ""),
        org_type=str(row["org_type"] or ""),
        punished_entities=str(
            row["punished_entities"]
            or row["summary_punished_entity"]
            or row["punished_entity"]
            or ""
        ),
        document_number=str(row["document_number"] or ""),
        is_fund_related=None if row["is_fund_related"] is None else bool(row["is_fund_related"]),
        fund_evidence=str(row["fund_evidence"] or ""),
        violation_type=str(row["violation_type"] or ""),
        punishment=str(row["punishment"] or ""),
        punishment_date=str(row["punishment_date"] or ""),
        involved_fund=str(row["involved_fund"] or ""),
        violation_summary=str(row["violation_summary"] or ""),
        legal_basis=str(row["legal_basis"] or ""),
        penalty_amount=str(row["penalty_amount"] or ""),
        market_ban=str(row["market_ban"] or ""),
        extract_time=str(row["extract_time"] or ""),
        fetch_time=str(row["fetch_time"] or ""),
        pdf_url=str(row["pdf_url"] or ""),
        doc_url=str(row["doc_url"] or ""),
        error=str(row["error"] or ""),
        has_body=bool(row["has_body"]),
    )
