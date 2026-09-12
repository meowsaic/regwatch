"""旧磁盘布局 ↔ SQLite 的批量导入导出。

- :func:`import_from_disk` 把旧 ``data/**`` 目录一次性灌进库（幂等、可重跑）；
- :func:`export_to_disk` 反向导出为同样的目录结构，可随时退回旧布局。

导入会读取 ``_summary_index.json`` 中的状态记录，因此
「已完成 / 非基金相关 / 失败」三种状态都能被正确还原。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain import CaseRecord, CaseStatus, Category, Dataset, SummaryRecord
from ..logging_setup import get_logger
from .jsonio import (
    case_from_disk,
    case_to_disk,
    infer_status,
    iter_case_files,
    iter_summary_files,
    read_json,
    read_summary_index,
    summary_from_disk,
    summary_to_disk,
    write_json,
)
from .repositories import CaseRepository, SummaryRepository

logger = get_logger("db.transfer")

__all__ = ["DiskLayout", "ImportReport", "export_to_disk", "import_from_disk"]


@dataclass(frozen=True, slots=True)
class DiskLayout:
    """旧磁盘布局的四个根目录。"""

    amac_cases: Path
    amac_summaries: Path
    csrc_cases: Path
    csrc_summaries: Path

    def root_for(self, dataset: Dataset, kind: str) -> Path:
        if kind == "cases":
            return self.amac_cases if dataset is Dataset.AMAC else self.csrc_cases
        return self.amac_summaries if dataset is Dataset.AMAC else self.csrc_summaries

    @classmethod
    def from_data_root(cls, root: Path | str) -> DiskLayout:
        """按默认 ``data/{amac,csrc}/{cases,summaries}`` 结构推导。"""
        base = Path(root)
        return cls(
            amac_cases=base / "amac" / "cases",
            amac_summaries=base / "amac" / "summaries",
            csrc_cases=base / "csrc" / "cases",
            csrc_summaries=base / "csrc" / "summaries",
        )


@dataclass(slots=True)
class ImportReport:
    """导入结果，供 CLI 打印与对账。

    ``cases`` / ``summaries`` 是**按主键去重后**实际写入的条数；
    ``duplicate_cases`` / ``duplicate_summaries`` 记录磁盘上同一 case_id
    出现多份、被合并覆盖的文件数（旧布局跨局重复归档时会出现）。
    """

    cases: dict[str, int] = field(default_factory=dict)
    summaries: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, dict[str, int]] = field(default_factory=dict)
    duplicate_cases: dict[str, int] = field(default_factory=dict)
    duplicate_summaries: dict[str, int] = field(default_factory=dict)
    skipped_files: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": dict(self.cases),
            "summaries": dict(self.summaries),
            "statuses": {k: dict(v) for k, v in self.statuses.items()},
            "duplicate_cases": dict(self.duplicate_cases),
            "duplicate_summaries": dict(self.duplicate_summaries),
            "skipped_files": self.skipped_files,
        }


def import_from_disk(
    cases_repo: CaseRepository,
    summaries_repo: SummaryRepository,
    layout: DiskLayout,
    datasets: tuple[Dataset, ...] = (Dataset.AMAC, Dataset.CSRC),
    *,
    batch_size: int = 500,
) -> ImportReport:
    """把旧磁盘布局导入 SQLite（可重复执行，按主键覆盖）。"""
    report = ImportReport()

    for dataset in datasets:
        index = read_summary_index(layout.root_for(dataset, "summaries"))

        summaries: dict[str, SummaryRecord] = {}
        duplicate_summaries = 0
        for _path, raw in iter_summary_files(layout.root_for(dataset, "summaries")):
            summary = summary_from_disk(dataset, raw)
            if not summary.case_id:
                report.skipped_files += 1
                continue
            if summary.case_id in summaries:
                duplicate_summaries += 1
            summaries[summary.case_id] = summary

        # 按主键去重：旧布局里同一案例可能跨局重复归档，后者覆盖前者
        cases: dict[str, CaseRecord] = {}
        duplicate_cases = 0
        for _path, raw in iter_case_files(layout.root_for(dataset, "cases")):
            case = case_from_disk(dataset, raw)
            if not case.case_id:
                report.skipped_files += 1
                continue
            info = index.get(case.case_id, {})
            existing_summary = summaries.get(case.case_id)
            case.status = infer_status(
                index_status=str(info.get("status", "")),
                has_summary=existing_summary is not None,
                extract_success=bool(existing_summary.extract_success)
                if existing_summary
                else True,
            )
            case.status_note = str(info.get("reason", "") or info.get("note", "") or "")
            if case.case_id in cases:
                duplicate_cases += 1
            cases[case.case_id] = case

        # 索引里存在、但磁盘上已无原文的案例（多为 skipped）也要建行
        for case_id, info in index.items():
            if case_id in cases or str(info.get("status", "")) != CaseStatus.SKIPPED.value:
                continue
            cases[case_id] = CaseRecord(
                dataset=dataset,
                case_id=case_id,
                status=CaseStatus.SKIPPED,
                status_note=str(info.get("reason", "") or ""),
            )

        written = cases_repo.upsert_many(cases.values(), batch_size=batch_size)

        summary_values = [
            summary
            for case_id, summary in summaries.items()
            if case_id in cases or dataset is Dataset.AMAC
        ]
        summary_written = summaries_repo.upsert_many(summary_values, batch_size=batch_size)

        key = dataset.value
        report.cases[key] = written
        report.summaries[key] = summary_written
        report.duplicate_cases[key] = duplicate_cases
        report.duplicate_summaries[key] = duplicate_summaries
        counter: dict[str, int] = {}
        for case in cases.values():
            counter[case.status.value] = counter.get(case.status.value, 0) + 1
        report.statuses[key] = counter
        logger.info(
            "导入 %s：案例 %d 条，摘要 %d 条，状态分布 %s",
            dataset.label,
            written,
            summary_written,
            counter,
        )

    return report


def export_to_disk(
    cases_repo: CaseRepository,
    summaries_repo: SummaryRepository,
    out_root: Path | str,
    datasets: tuple[Dataset, ...] = (Dataset.AMAC, Dataset.CSRC),
) -> dict[str, int]:
    """把库内容导出为旧磁盘布局（可用于回退或交付）。"""
    root = Path(out_root)
    counts: dict[str, int] = {"cases": 0, "summaries": 0}

    for dataset in datasets:
        cases_dir = root / dataset.value / "cases"
        summaries_dir = root / dataset.value / "summaries"

        for case_id in cases_repo.ids(dataset):
            case = cases_repo.get_with_body(dataset, case_id)
            if case is None:
                continue
            target = cases_dir / _case_subpath(case) / f"{case_id}.json"
            write_json(target, case_to_disk(case))
            counts["cases"] += 1

            summary = summaries_repo.get(dataset, case_id)
            if summary is None:
                continue
            summary_target = summaries_dir / _summary_subpath(case) / f"{case_id}_summary.json"
            write_json(summary_target, summary_to_disk(summary, case))
            counts["summaries"] += 1

    return counts


def _case_subpath(case: CaseRecord) -> str:
    """案例原文的存放子目录（沿用旧布局）。"""
    if case.dataset is Dataset.CSRC:
        parts = (case.bureau or "Unknown", case.case_type.value or "measure")
        return "/".join(part for part in parts if part)
    return "institution" if case.category is Category.INSTITUTION else "personnel"


def _summary_subpath(case: CaseRecord) -> str:
    """摘要的存放子目录（沿用旧布局）。"""
    if case.dataset is Dataset.CSRC:
        return "/".join(
            part for part in (case.bureau or "Unknown", case.case_type.value or "measure") if part
        )
    return ""


def load_legacy_index(cases_dir: Path, dataset: Dataset) -> dict[str, Any]:
    """读取旧抓取索引（``_index.json``），供断点续传状态迁移使用。"""
    return read_json(Path(cases_dir) / "_index.json")
