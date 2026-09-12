"""一次性把旧 ``data/`` JSON 布局导入 SQLite。

用法::

    uv run python scripts/migrate_to_sqlite.py --dry-run    # 先看会导入什么
    uv run python scripts/migrate_to_sqlite.py              # 真正导入并对账

脚本**只读**原 JSON 目录、**只写**新库，原目录在验证期内保持不动，
因此可以随时重跑（按主键覆盖，幂等）。

对账按**唯一 case_id** 比对（同一 case_id 的重复文件会按主键合并），
并额外报告磁盘上的重复归档情况；库中记录数少于磁盘唯一 case_id 时
以非零退出码结束并提示检查。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from regwatch.db import DataStore
from regwatch.db.jsonio import iter_case_files, iter_summary_files
from regwatch.db.transfer import DiskLayout, ImportReport, import_from_disk
from regwatch.domain import Dataset
from regwatch.settings import PROJECT_ROOT, get_settings

#: 目录扫描器类型：传入根目录，产出 (文件路径, JSON 数据)
FileScanner = Callable[[Path], Iterator[tuple[Path, dict[str, Any]]]]


def _count_files(layout: DiskLayout, dataset: Dataset) -> tuple[int, int]:
    cases = sum(1 for _ in iter_case_files(layout.root_for(dataset, "cases")))
    summaries = sum(1 for _ in iter_summary_files(layout.root_for(dataset, "summaries")))
    return cases, summaries


def _unique_ids(root: Path, scan: FileScanner) -> set[str]:
    """磁盘上唯一的 case_id 集合（同一 ID 多份文件只算一次）。"""
    return {str(raw.get("case_id") or "") for _path, raw in scan(root)} - {""}


def _verify(store: DataStore, layout: DiskLayout) -> list[str]:
    """按唯一 case_id 对账，返回问题列表（空列表表示通过）。"""
    problems: list[str] = []
    for dataset in (Dataset.AMAC, Dataset.CSRC):
        disk_case_ids = _unique_ids(layout.root_for(dataset, "cases"), iter_case_files)
        disk_summary_ids = _unique_ids(layout.root_for(dataset, "summaries"), iter_summary_files)
        db_case_ids = set(store.cases.ids(dataset))
        db_summaries = store.summaries.count(dataset)

        if len(db_case_ids) < len(disk_case_ids):
            file_count, _ = _count_files(layout, dataset)
            problems.append(
                f"{dataset.label}：库中案例 {len(db_case_ids)} 条少于磁盘唯一 case_id "
                f"{len(disk_case_ids)} 个（磁盘文件 {file_count} 个）"
            )
        # CSRC 摘要必须依附已入库的案例，AMAC 的孤儿摘要也允许入库
        expected_summaries = (
            len(disk_summary_ids)
            if dataset is Dataset.AMAC
            else len(disk_summary_ids & db_case_ids)
        )
        if db_summaries < expected_summaries:
            problems.append(
                f"{dataset.label}：库中摘要 {db_summaries} 条少于应导入的 {expected_summaries} 条"
            )
    return problems


def _duplicate_notes(report: ImportReport) -> list[str]:
    """磁盘上同一 case_id 有多份文件时的提示（已按主键合并，不算对账失败）。"""
    notes: list[str] = []
    for dataset in (Dataset.AMAC, Dataset.CSRC):
        cases = report.duplicate_cases.get(dataset.value, 0)
        summaries = report.duplicate_summaries.get(dataset.value, 0)
        if cases or summaries:
            notes.append(
                f"{dataset.label}：重复案例文件 {cases} 个、重复摘要文件 {summaries} 个"
                "（同一 case_id 多份，已按主键合并）"
            )
    return notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把旧 data/ JSON 布局导入 SQLite")
    parser.add_argument("--data-root", default="data", help="旧数据根目录（相对项目根）")
    parser.add_argument("--database", default="", help="目标库文件路径（默认取 config.json）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    parser.add_argument("--no-verify", action="store_true", help="跳过导入后对账")
    args = parser.parse_args(argv)

    data_root = Path(args.data_root)
    if not data_root.is_absolute():
        data_root = PROJECT_ROOT / data_root
    layout = DiskLayout.from_data_root(data_root)

    if not layout.amac_cases.is_dir() and not layout.csrc_cases.is_dir():
        print(f"未找到旧数据目录：{data_root}（可通过 --data-root 指定）")
        return 1

    if args.dry_run:
        summary: dict[str, dict[str, int]] = {}
        for dataset in (Dataset.AMAC, Dataset.CSRC):
            files_cases, files_summaries = _count_files(layout, dataset)
            summary[dataset.value] = {
                "cases": files_cases,
                "unique_cases": len(
                    _unique_ids(layout.root_for(dataset, "cases"), iter_case_files)
                ),
                "summaries": files_summaries,
                "unique_summaries": len(
                    _unique_ids(layout.root_for(dataset, "summaries"), iter_summary_files)
                ),
            }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("（dry-run，未写入任何数据）")
        return 0

    target = Path(args.database) if args.database else get_settings().database
    print(f"目标数据库：{target}")
    store = DataStore.open(target)

    report = import_from_disk(store.cases, store.summaries, layout)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))

    if not args.no_verify:
        problems = _verify(store, layout)
        if problems:
            print("对账未通过：", file=sys.stderr)
            for item in problems:
                print(f"  - {item}", file=sys.stderr)
            return 2
        for note in _duplicate_notes(report):
            print(f"注意：{note}")
        print("对账通过：库中记录数不少于磁盘唯一 case_id 数。")

    store.touch()
    print(f"完成。可将 config.json 的 database 指向 {target}（已自动切换则忽略）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
