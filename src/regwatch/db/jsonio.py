"""旧磁盘 JSON ↔ 领域记录的双向转换。

迁移期与 ``db export`` / ``db import`` 命令共用：

- :func:`case_from_disk` / :func:`summary_from_disk` 读取旧布局的 JSON，
  字段名沿用磁盘上的历史命名（``scfjg``、``is_fund_related`` 等），容错缺字段；
- :func:`case_to_disk` / :func:`summary_to_disk` 反向写回同样的结构，
  导出的文件与迁移前完全兼容，可随时退回旧布局。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..domain import CaseRecord, CaseStatus, Dataset, SummaryRecord, split_multi_value

logger = logging.getLogger("regwatch.db.jsonio")

__all__ = [
    "case_from_disk",
    "case_to_disk",
    "infer_status",
    "iter_case_files",
    "iter_summary_files",
    "read_json",
    "read_summary_index",
    "summary_from_disk",
    "summary_to_disk",
    "write_json",
]

#: 文件名以下划线开头的是索引 / 缓存，不是案例文件
_INDEX_PREFIX = "_"


# ──────────────────────────── 原子读写 ────────────────────────────


def read_json(path: Path) -> dict[str, Any]:
    """读取 JSON；缺失、损坏或非对象时返回空字典。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("JSON 读取失败，已跳过：%s（%s）", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """原子写入 JSON（临时文件 + ``os.replace``），父目录自动创建。"""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # pragma: no cover - 极少发生
                pass


# ──────────────────────────── 目录扫描 ────────────────────────────


def iter_case_files(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """遍历案例原文目录，跳过索引与缓存文件。"""
    root = Path(root)
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.json")):
        if path.name.startswith(_INDEX_PREFIX):
            continue
        data = read_json(path)
        if data:
            yield path, data


def iter_summary_files(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """遍历摘要目录（``*_summary.json``），跳过索引文件。"""
    root = Path(root)
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*_summary.json")):
        if path.name.startswith(_INDEX_PREFIX):
            continue
        data = read_json(path)
        if data:
            yield path, data


def read_summary_index(summaries_dir: Path) -> dict[str, dict[str, Any]]:
    """读取 ``_summary_index.json`` 的 ``cases`` 段。"""
    raw = read_json(Path(summaries_dir) / "_summary_index.json")
    cases = raw.get("cases")
    return {str(k): dict(v) for k, v in cases.items()} if isinstance(cases, dict) else {}


def infer_status(
    *,
    index_status: str = "",
    has_summary: bool = False,
    extract_success: bool = True,
) -> CaseStatus:
    """推断案例状态：索引记录优先，其次看摘要文件是否存在。"""
    status = CaseStatus.parse(index_status)
    if status is not None:
        return status
    if has_summary:
        return CaseStatus.DONE if extract_success else CaseStatus.FAILED
    return CaseStatus.PENDING


# ──────────────────────────── 案例 ────────────────────────────


def case_from_disk(
    dataset: Dataset, raw: dict[str, Any], *, default_status: str = ""
) -> CaseRecord:
    """把磁盘上的案例 JSON 转为 :class:`CaseRecord`。

    ``raw`` 中缺少的字段一律取默认值；``raw_text`` 若存在会一并放入记录。
    """
    dataset = Dataset.parse(dataset, Dataset.AMAC) or Dataset.AMAC

    # 数据集专属字段一次性交给构造函数，避免事后赋值绕过 __post_init__ 的枚举归一化
    extra: dict[str, Any]
    if dataset is Dataset.AMAC:
        extra = {
            "category": str(raw.get("category") or ""),
            "org_type": str(raw.get("org_type") or ""),
            "punished_entity": str(raw.get("punished_entity") or ""),
            "source_type": str(raw.get("source_type") or ""),
            "ocr_success": bool(raw.get("ocr_success")),
        }
    else:
        raw_flag = raw.get("is_fund_related")
        extra = {
            "case_type": str(raw.get("case_type") or ""),
            "bureau": str(raw.get("bureau") or ""),
            "document_number": str(raw.get("document_number") or ""),
            "punished_entities": str(raw.get("punished_entities") or ""),
            "fund_evidence": str(raw.get("fund_evidence") or ""),
            "doc_url": str(raw.get("doc_url") or ""),
            "is_fund_related": None if raw_flag is None else bool(raw_flag),
        }

    return CaseRecord(
        dataset=dataset,
        case_id=str(raw.get("case_id") or ""),
        source_url=str(raw.get("source_url") or ""),
        title=str(raw.get("title") or ""),
        date=str(raw.get("date") or ""),
        status=default_status or "pending",
        fetch_time=str(raw.get("fetch_time") or ""),
        error=str(raw.get("error") or ""),
        pdf_url=str(raw.get("pdf_url") or ""),
        raw_text=str(raw.get("raw_text") or ""),
        **extra,
    )


def case_to_disk(case: CaseRecord) -> dict[str, Any]:
    """把 :class:`CaseRecord` 写成与旧布局兼容的 JSON 结构。"""
    data: dict[str, Any] = {
        "case_id": case.case_id,
        "source_url": case.source_url,
        "title": case.title,
        "date": case.date,
        "raw_text": case.raw_text,
        "fetch_time": case.fetch_time,
        "error": case.error,
        "pdf_url": case.pdf_url,
    }
    if case.dataset is Dataset.AMAC:
        data.update(
            {
                "source_type": case.source_type,
                "category": case.category.value,
                "ocr_success": case.ocr_success,
                "org_type": case.org_type,
                "punished_entity": case.punished_entity,
            }
        )
    else:
        data.update(
            {
                "case_type": case.case_type.value,
                "bureau": case.bureau,
                "document_number": case.document_number,
                "punished_entities": case.punished_entities,
                "is_fund_related": case.is_fund_related,
                "fund_evidence": case.fund_evidence,
                "doc_url": case.doc_url,
            }
        )
    return data


# ──────────────────────────── 摘要 ────────────────────────────


def summary_from_disk(dataset: Dataset, raw: dict[str, Any]) -> SummaryRecord:
    """把磁盘上的摘要 JSON 转为 :class:`SummaryRecord`。"""
    return SummaryRecord(
        dataset=Dataset.parse(dataset, Dataset.AMAC) or Dataset.AMAC,
        case_id=str(raw.get("case_id") or ""),
        entity_type=str(raw.get("entity_type") or ""),
        punished_entity=str(raw.get("punished_entity") or ""),
        violation_type=str(raw.get("violation_type") or ""),
        punishment=str(raw.get("punishment") or ""),
        punishment_date=str(raw.get("punishment_date") or ""),
        involved_fund=str(raw.get("involved_fund") or ""),
        violation_summary=str(raw.get("violation_summary") or ""),
        legal_basis=str(raw.get("legal_basis") or ""),
        penalty_amount=str(raw.get("penalty_amount") or ""),
        market_ban=str(raw.get("market_ban") or ""),
        extract_success=bool(raw.get("extract_success")),
        error=str(raw.get("error") or ""),
        extract_time=str(raw.get("extract_time") or ""),
        llm_provider=str(raw.get("llm_provider") or ""),
        llm_model=str(raw.get("llm_model") or ""),
    )


def summary_to_disk(summary: SummaryRecord, case: CaseRecord | None = None) -> dict[str, Any]:
    """把 :class:`SummaryRecord` 写成与旧布局兼容的 JSON 结构。

    ``case`` 提供标题、日期、当事人等冗余字段（旧摘要文件里也存了一份）。
    """
    data: dict[str, Any] = {
        "case_id": summary.case_id,
        "entity_type": summary.entity_type,
        "punished_entity": summary.punished_entity or (case.punished_entity if case else ""),
        "violation_type": summary.violation_type,
        "punishment": summary.punishment,
        "involved_fund": summary.involved_fund,
        "violation_summary": summary.violation_summary,
        "legal_basis": summary.legal_basis,
        "extract_success": summary.extract_success,
        "error": summary.error,
        "extract_time": summary.extract_time,
    }

    if summary.dataset is Dataset.AMAC:
        data.update(
            {
                "source_url": case.source_url if case else "",
                "category": case.category.value if case else "",
                "title": case.title if case else "",
                "date": case.date if case else "",
                "org_type": case.org_type if case else "",
                "punishment_date": summary.punishment_date,
            }
        )
    else:
        data.update(
            {
                "source_url": case.source_url if case else "",
                "case_type": case.case_type.value if case else "",
                "bureau": case.bureau if case else "",
                "title": case.title if case else "",
                "date": case.date if case else "",
                "document_number": case.document_number if case else "",
                "punished_entities": case.punished_entities if case else "",
                "is_fund_related": case.is_fund_related if case else None,
                "fund_evidence": case.fund_evidence if case else "",
                "pdf_url": case.pdf_url if case else "",
                "penalty_amount": summary.penalty_amount,
                "market_ban": summary.market_ban,
                "llm_provider": summary.llm_provider,
                "llm_model": summary.llm_model,
            }
        )
    return data


def violations_of(summary: SummaryRecord) -> tuple[str, ...]:
    """摘要的违规类型（已拆分）。"""
    return split_multi_value(summary.violation_type)
