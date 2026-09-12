"""数据层测试：仓储 CRUD、查询编译、迁移与 JSON 导入导出。"""

from __future__ import annotations

from pathlib import Path

import pytest

from regwatch.db import DataStore
from regwatch.db.jsonio import case_to_disk, summary_to_disk, write_json
from regwatch.db.migrations import current_version, ensure_schema
from regwatch.db.transfer import DiskLayout, export_to_disk, import_from_disk
from regwatch.domain import (
    CaseQuery,
    CaseRecord,
    CaseStatus,
    Dataset,
    JobStatus,
    SummaryRecord,
    TaskLogLine,
    TaskRecord,
)


@pytest.fixture
def seeded(store: DataStore, amac_case: CaseRecord, csrc_case: CaseRecord) -> DataStore:
    store.cases.upsert(amac_case)
    store.cases.upsert(csrc_case)
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.AMAC,
            case_id=amac_case.case_id,
            entity_type="机构",
            violation_type="违规募集、内控缺失",
            punishment="公开谴责",
            legal_basis="《私募投资基金监督管理暂行办法》第三十八条",
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.CSRC,
            case_id=csrc_case.case_id,
            entity_type="机构",
            violation_type="信息披露违规",
            punishment="罚款",
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )
    return store


class TestSchema:
    def test_ensure_schema_is_idempotent(self, store: DataStore) -> None:
        ensure_schema(store.db.connect())
        assert current_version(store.db.connect()) >= 1
        assert ensure_schema(store.db.connect()) >= 1

    def test_revision_monotonic(self, store: DataStore) -> None:
        assert store.revision() == 0
        assert store.touch() == 1
        assert store.touch() == 2

    def test_v2_upgrade_rebuilds_canonical_violations(self, seeded: DataStore) -> None:
        """v1 → v2 升级：关联表里的旧口径片段被重建为 canonical 类型。"""
        amac_id = seeded.cases.ids(Dataset.AMAC)[0]
        with seeded.db.transaction() as conn:
            conn.execute(
                "UPDATE summaries SET violation_type = ? WHERE dataset = 'amac' AND case_id = ?",
                ("适当性管理不到位", amac_id),
            )
            conn.execute("DELETE FROM case_violations")
            conn.execute(
                "INSERT INTO case_violations (dataset, case_id, violation) VALUES (?, ?, ?)",
                ("amac", amac_id, "适当性管理不到位"),
            )
            conn.execute("PRAGMA user_version = 1")

        assert ensure_schema(seeded.db.connect()) == 1
        assert current_version(seeded.db.connect()) >= 2
        assert seeded.summaries.violations_of(Dataset.AMAC, amac_id) == ("违规募集",)


class TestCaseRepository:
    def test_upsert_stores_body_separately(self, store: DataStore, amac_case: CaseRecord) -> None:
        store.cases.upsert(amac_case)
        assert store.cases.get(Dataset.AMAC, amac_case.case_id) is not None
        assert store.cases.get_body(Dataset.AMAC, amac_case.case_id).startswith("经查")
        assert store.cases.get(Dataset.AMAC, amac_case.case_id).raw_text == ""

    def test_upsert_does_not_downgrade_finished_status(
        self, store: DataStore, amac_case: CaseRecord
    ) -> None:
        store.cases.upsert(amac_case)
        store.cases.set_status(Dataset.AMAC, amac_case.case_id, CaseStatus.DONE)
        store.cases.upsert(amac_case.replace(status=CaseStatus.PENDING))
        assert store.cases.get(Dataset.AMAC, amac_case.case_id).status is CaseStatus.DONE

    def test_failed_can_retry_to_pending(self, store: DataStore, amac_case: CaseRecord) -> None:
        store.cases.upsert(amac_case)
        store.cases.set_status(Dataset.AMAC, amac_case.case_id, CaseStatus.FAILED)
        store.cases.upsert(amac_case)
        assert store.cases.get(Dataset.AMAC, amac_case.case_id).status is CaseStatus.PENDING

    def test_search_filters_by_dataset_and_keyword(self, seeded: DataStore) -> None:
        assert len(seeded.cases.search(CaseQuery())) == 2
        assert len(seeded.cases.search(CaseQuery(datasets=(Dataset.AMAC,)))) == 1
        assert len(seeded.cases.search(CaseQuery(keyword="纪律处分"))) == 1

    def test_search_escapes_like_wildcards(self, seeded: DataStore) -> None:
        assert seeded.cases.search(CaseQuery(keyword="%")) == []

    def test_search_by_violation_and_status(self, seeded: DataStore) -> None:
        assert len(seeded.cases.search(CaseQuery(violations=("内控缺失",)))) == 1
        assert len(seeded.cases.search(CaseQuery(statuses=(CaseStatus.DONE,)))) == 2
        assert len(seeded.cases.search(CaseQuery(statuses=(CaseStatus.PENDING,)))) == 0

    def test_search_date_range(self, seeded: DataStore) -> None:
        assert len(seeded.cases.search(CaseQuery(date_from="2026-01-01"))) == 2
        assert len(seeded.cases.search(CaseQuery(date_from="2026-02-01"))) == 0

    def test_search_pagination(self, seeded: DataStore) -> None:
        first = seeded.cases.search(CaseQuery(limit=1, offset=0))
        second = seeded.cases.search(CaseQuery(limit=1, offset=1))
        assert first[0].case_id != second[0].case_id

    def test_aggregate_and_trend(self, seeded: DataStore) -> None:
        assert dict(seeded.cases.aggregate(CaseQuery(), "dataset")) == {"amac": 1, "csrc": 1}
        assert seeded.cases.time_trend(CaseQuery()) == [
            {"period": "2026-01", "count": 2, "institutions": 2, "personnel": 0}
        ]

    def test_time_trend_with_dataset_filter_keeps_monthly_periods(self, store: DataStore) -> None:
        """回归：`dataset IN (?, ?)` 不得与 substr/length 占位符抢绑定位置。

        网页总览默认 `datasets=Dataset.all()`，若 length 参数被 append 到
        WHERE 参数之后，substr 会吃到 dataset 值，period 变成空串。
        """
        store.cases.upsert(
            CaseRecord(
                dataset=Dataset.AMAC,
                case_id="a1",
                date="2025-03-01",
                status=CaseStatus.DONE,
            )
        )
        store.cases.upsert(
            CaseRecord(
                dataset=Dataset.CSRC,
                case_id="c1",
                date="2025-04-01",
                status=CaseStatus.DONE,
            )
        )
        store.summaries.upsert(
            SummaryRecord(
                dataset=Dataset.AMAC,
                case_id="a1",
                entity_type="机构",
            ),
            status=CaseStatus.DONE,
        )
        store.summaries.upsert(
            SummaryRecord(
                dataset=Dataset.CSRC,
                case_id="c1",
                entity_type="个人",
            ),
            status=CaseStatus.DONE,
        )
        query = CaseQuery(datasets=Dataset.all())
        assert store.cases.time_trend(query) == [
            {"period": "2025-03", "count": 1, "institutions": 1, "personnel": 0},
            {"period": "2025-04", "count": 1, "institutions": 0, "personnel": 1},
        ]
        assert store.cases.time_trend(query, granularity="year") == [
            {"period": "2025", "count": 2, "institutions": 1, "personnel": 1},
        ]

    def test_aggregate_rejects_unknown_group(self, seeded: DataStore) -> None:
        with pytest.raises(ValueError):
            seeded.cases.aggregate(CaseQuery(), "raw_text")

    def test_aggregate_merges_null_and_empty_group(self, store: DataStore) -> None:
        """回归：NULL 与空串应合并为同一组，而不是两行同名结果。"""
        store.cases.upsert(CaseRecord(dataset=Dataset.AMAC, case_id="a1"))
        store.cases.upsert(CaseRecord(dataset=Dataset.CSRC, case_id="c1"))
        store.summaries.upsert(
            SummaryRecord(dataset=Dataset.AMAC, case_id="a1", entity_type="机构")
        )
        grouped = dict(store.cases.aggregate(CaseQuery(), "entity_type"))
        assert grouped == {"机构": 1, "": 1}

    def test_delete_cascades_body_and_summary(self, seeded: DataStore) -> None:
        case_id = seeded.cases.ids(Dataset.AMAC)[0]
        assert seeded.cases.delete(Dataset.AMAC, case_id)
        assert seeded.cases.get_body(Dataset.AMAC, case_id) == ""
        assert seeded.summaries.get(Dataset.AMAC, case_id) is None

    def test_iter_cases_yields_body(self, seeded: DataStore) -> None:
        cases = list(seeded.cases.iter_cases(Dataset.AMAC))
        assert cases and cases[0].raw_text.startswith("经查")

    def test_totals(self, seeded: DataStore) -> None:
        assert seeded.cases.totals()["all"] == 2
        assert seeded.cases.totals()["bodies"] == 2


class TestSummaryRepository:
    def test_violations_are_expanded(self, seeded: DataStore) -> None:
        case_id = seeded.cases.ids(Dataset.AMAC)[0]
        assert seeded.summaries.violations_of(Dataset.AMAC, case_id) == ("内控缺失", "违规募集")

    def test_violation_distribution_counts_each_type(self, seeded: DataStore) -> None:
        distribution = dict(seeded.summaries.violation_distribution(CaseQuery()))
        assert distribution["违规募集"] == 1
        assert distribution["信息披露违规"] == 1

    def test_violation_options(self, seeded: DataStore) -> None:
        assert set(seeded.summaries.violation_options()) == {
            "内控缺失",
            "违规募集",
            "信息披露违规",
        }

    def test_violations_are_stored_as_canonical(
        self, store: DataStore, amac_case: CaseRecord
    ) -> None:
        """关联表存 canonical 类型：模型输出的子项表述与跨数据集同义措辞都会归一。"""
        store.cases.upsert(amac_case)
        store.summaries.upsert(
            SummaryRecord(
                dataset=Dataset.AMAC,
                case_id=amac_case.case_id,
                violation_type="适当性管理不到位、未勤勉尽责",
            ),
            status=CaseStatus.DONE,
        )
        assert store.summaries.violations_of(Dataset.AMAC, amac_case.case_id) == (
            "未尽勤勉尽责义务",
            "违规募集",
        )

    def test_rebuild_violations_backfills_history(
        self, store: DataStore, amac_case: CaseRecord
    ) -> None:
        """分类体系升级后 rebuild：不重新调用模型即可把历史关联表换成 canonical。"""
        store.cases.upsert(amac_case)
        store.summaries.upsert(
            SummaryRecord(
                dataset=Dataset.AMAC,
                case_id=amac_case.case_id,
                violation_type="适当性管理不到位",
            ),
            status=CaseStatus.DONE,
        )
        # 模拟历史数据：关联表里存的还是未归一的原始片段
        with store.db.transaction() as conn:
            conn.execute("DELETE FROM case_violations")
            conn.execute(
                "INSERT INTO case_violations (dataset, case_id, violation) VALUES (?, ?, ?)",
                ("amac", amac_case.case_id, "适当性管理不到位"),
            )

        assert store.summaries.rebuild_violations() == 1
        assert store.summaries.violations_of(Dataset.AMAC, amac_case.case_id) == ("违规募集",)
        assert dict(store.summaries.violation_distribution(CaseQuery())) == {"违规募集": 1}

    def test_upsert_updates_case_status(self, store: DataStore, amac_case: CaseRecord) -> None:
        store.cases.upsert(amac_case)
        store.summaries.upsert(
            SummaryRecord(dataset=Dataset.AMAC, case_id=amac_case.case_id, violation_type="其他"),
            status=CaseStatus.SKIPPED,
            note="非基金相关",
        )
        case = store.cases.get(Dataset.AMAC, amac_case.case_id)
        assert case.status is CaseStatus.SKIPPED
        assert case.status_note == "非基金相关"


class TestTaskRepository:
    def test_save_get_and_progress(self, store: DataStore, task_record: TaskRecord) -> None:
        store.tasks.save(task_record)
        assert store.tasks.get("abc123").title == "测试任务"
        store.tasks.update_progress("abc123", processed=5, total=10, message="进行中")
        record = store.tasks.get("abc123")
        assert record.processed == 5 and record.total == 10

    def test_finish_and_logs(self, store: DataStore, task_record: TaskRecord) -> None:
        store.tasks.save(task_record)
        store.tasks.append_log(
            TaskLogLine(task_id="abc123", seq=0, message="开始", created_at="2026-01-01T00:00:00")
        )
        store.tasks.finish("abc123", JobStatus.SUCCESS, result={"ok": True})
        record = store.tasks.get("abc123")
        assert record.result == {"ok": True}
        assert len(store.tasks.logs("abc123")) == 1
        assert store.tasks.log_count("abc123") == 1

    def test_prune_keeps_most_recent(self, store: DataStore) -> None:
        for index in range(5):
            record = TaskRecord(
                id=f"t{index}", kind="summarize", created_at=f"2026-01-0{index + 1}T00:00:00"
            )
            store.tasks.save(record)
            store.tasks.finish(record.id, JobStatus.SUCCESS)
        assert store.tasks.prune(keep=2) == 3
        assert len(store.tasks.list()) == 2


class TestMetaRepository:
    def test_org_type_cache(self, store: DataStore) -> None:
        store.meta.set_org_type("某某基金", "私募证券投资基金管理人", "test")
        assert store.meta.org_type("某某基金") == "私募证券投资基金管理人"
        assert store.meta.org_types() == {"某某基金": "私募证券投资基金管理人"}

    def test_fetch_state(self, store: DataStore) -> None:
        store.meta.set_fetch_state("csrc", "Beijing/penalty", "page", "3")
        assert store.meta.fetch_state("csrc", "Beijing/penalty", "page") == "3"
        assert store.meta.fetch_states("csrc", "Beijing/penalty") == {"page": "3"}


class TestJsonTransfer:
    def _layout(self, root: Path, amac_case: CaseRecord) -> DiskLayout:
        layout = DiskLayout.from_data_root(root)
        write_json(
            layout.amac_cases / "institution" / f"{amac_case.case_id}.json",
            case_to_disk(amac_case),
        )
        write_json(
            layout.amac_summaries / "_summary_index.json",
            {"cases": {amac_case.case_id: {"status": "done"}}},
        )
        write_json(
            layout.amac_summaries / f"{amac_case.case_id}_summary.json",
            summary_to_disk(
                SummaryRecord(
                    dataset=Dataset.AMAC,
                    case_id=amac_case.case_id,
                    violation_type="违规募集",
                    punishment="公开谴责",
                    extract_success=True,
                ),
                amac_case,
            ),
        )
        return layout

    def test_import_then_export_roundtrip(
        self, store: DataStore, tmp_path: Path, amac_case: CaseRecord
    ) -> None:
        layout = self._layout(tmp_path / "data", amac_case)
        report = import_from_disk(store.cases, store.summaries, layout)

        assert report.cases["amac"] == 1
        assert report.summaries["amac"] == 1
        assert report.statuses["amac"] == {"done": 1}

        counts = export_to_disk(store.cases, store.summaries, tmp_path / "export")
        assert counts["cases"] == 1
        assert counts["summaries"] == 1
        assert (tmp_path / "export" / "amac" / "cases" / "institution").is_dir()

    def test_import_is_idempotent(
        self, store: DataStore, tmp_path: Path, amac_case: CaseRecord
    ) -> None:
        layout = self._layout(tmp_path / "data", amac_case)
        import_from_disk(store.cases, store.summaries, layout)
        import_from_disk(store.cases, store.summaries, layout)
        assert len(store.cases.ids(Dataset.AMAC)) == 1

    def test_import_dedupes_case_id_across_subdirs(
        self, store: DataStore, tmp_path: Path, amac_case: CaseRecord
    ) -> None:
        """同一 case_id 在多个子目录重复归档时按主键合并，并在报告中计数。"""
        layout = self._layout(tmp_path / "data", amac_case)
        write_json(
            layout.amac_cases / "personnel" / f"{amac_case.case_id}.json",
            case_to_disk(amac_case),
        )
        write_json(
            layout.amac_summaries / "personnel" / f"{amac_case.case_id}_summary.json",
            summary_to_disk(
                SummaryRecord(
                    dataset=Dataset.AMAC, case_id=amac_case.case_id, extract_success=True
                ),
                amac_case,
            ),
        )

        report = import_from_disk(store.cases, store.summaries, layout)

        assert report.cases["amac"] == 1
        assert report.duplicate_cases["amac"] == 1
        assert report.summaries["amac"] == 1
        assert report.duplicate_summaries["amac"] == 1
        assert len(store.cases.ids(Dataset.AMAC)) == 1
