"""任务编排测试：参数规格、分发执行、持久化与取消。"""

from __future__ import annotations

import pytest

from regwatch.db import DataStore
from regwatch.domain import CaseRecord, Dataset, JobKind, JobStatus, TaskLogLine
from regwatch.services import JOB_SPECS, JobError, JobManager, JobRunner, Services
from regwatch.services.jobs import describe_spec


class TestParamSpec:
    def test_coerce_bool_from_text(self) -> None:
        spec = JOB_SPECS[JobKind.SUMMARIZE].fields[2]
        assert spec.name == "retry_failed"
        assert spec.coerce("true") is True
        assert spec.coerce("no") is False
        assert spec.coerce(None) is True  # 回落到默认值

    def test_coerce_int_with_invalid_input(self) -> None:
        spec = JOB_SPECS[JobKind.SUMMARIZE].fields[1]
        assert spec.coerce("5") == 5
        assert spec.coerce("abc") == 0

    def test_normalize_drops_unknown_keys(self) -> None:
        spec = JOB_SPECS[JobKind.SUMMARIZE]
        normalized = spec.normalize({"dataset": "amac", "unknown": 1, "workers": "3"})
        assert normalized == {"dataset": "amac", "workers": 3, "retry_failed": True, "limit": 0}

    def test_coerce_date_keeps_iso(self) -> None:
        from datetime import date

        spec = JOB_SPECS[JobKind.FETCH_AMAC].fields[0]
        assert spec.kind == "date"
        assert spec.coerce("2026-01-05") == "2026-01-05"
        assert spec.coerce(date(2026, 2, 3)) == "2026-02-03"
        assert spec.coerce(None) == ""
        assert spec.coerce("") == ""

    def test_date_fields_are_marked_date_kind(self) -> None:
        for kind in (JobKind.FETCH_AMAC, JobKind.FETCH_CSRC, JobKind.REPORT):
            for field in JOB_SPECS[kind].fields:
                if field.name in {"start_date", "end_date"}:
                    assert field.kind == "date"
        with pytest.raises(JobError):
            describe_spec("nope")

    def test_every_kind_has_a_spec(self) -> None:
        assert set(JOB_SPECS) == set(JobKind.all())


class TestRunner:
    def test_summarize_dispatches_and_merges(self, services: Services, store: DataStore) -> None:
        store.cases.upsert(
            CaseRecord(dataset=Dataset.AMAC, case_id="P1", title="t", raw_text="x" * 60)
        )
        services.llm.payload = {  # type: ignore[union-attr]
            "entity_type": "机构",
            "violation_type": "其他",
            "violation_summary": "摘要",
        }
        result = JobRunner(services).run(
            JobKind.SUMMARIZE, {"dataset": "all"}, lambda *args: None, lambda: False
        )
        assert result["total"] == 1
        assert result["success"] == 1

    def test_unknown_kind_raises(self, services: Services) -> None:
        with pytest.raises(JobError):
            JobRunner(services).run("nope", {}, lambda *args: None, lambda: False)

    def test_bad_dataset_raises(self, services: Services) -> None:
        with pytest.raises(JobError):
            JobRunner(services).run(
                JobKind.SUMMARIZE, {"dataset": "nope"}, lambda *args: None, lambda: False
            )


class TestJobManager:
    def test_submit_persists_and_completes(self, services: Services) -> None:
        job_id = services.jobs.submit(JobKind.SUMMARIZE, {"dataset": "amac"})
        record = services.jobs.wait(job_id, timeout=10)

        assert record is not None
        assert record.status is JobStatus.SUCCESS
        assert services.store.tasks.get(job_id) is not None
        # 任务日志既进内存缓冲也落库
        assert services.jobs.logs(job_id)
        assert services.store.tasks.logs(job_id)

    def test_same_kind_cannot_run_twice(self, services: Services) -> None:
        services.jobs.submit(JobKind.SUMMARIZE, {})
        with pytest.raises(JobError):
            services.jobs.submit(JobKind.SUMMARIZE, {})

    def test_cancel_marks_cancelled(self, services: Services) -> None:
        job_id = services.jobs.submit(JobKind.ORG_TYPE, {"dry_run": True})
        services.jobs.cancel(job_id)
        record = services.jobs.get(job_id)
        assert record is not None
        assert record.status in (JobStatus.CANCELLED, JobStatus.SUCCESS)

    def test_get_returns_fresh_snapshot(self, services: Services) -> None:
        job_id = services.jobs.submit(JobKind.SUMMARIZE, {})
        services.jobs.wait(job_id, timeout=10)
        first = services.jobs.get(job_id)
        assert first is not None
        first.title = "被外部改动"
        assert services.jobs.get(job_id).title != "被外部改动"

    def test_log_sink_persists_lines(self, services: Services) -> None:
        from regwatch.domain import TaskRecord

        services.store.tasks.save(TaskRecord(id="t1", kind="summarize"))
        services.jobs._persist_log(TaskLogLine(task_id="t1", seq=0, message="hello"))
        assert [line.message for line in services.store.tasks.logs("t1")] == ["hello"]

    def test_log_sink_ignores_foreign_tasks(self, services: Services) -> None:
        services.jobs._persist_log(TaskLogLine(task_id="unknown", seq=0, message="x"))
        assert services.store.tasks.logs("unknown") == []

    def test_jobs_lists_newest_first(self, services: Services) -> None:
        first = services.jobs.submit(JobKind.SUMMARIZE, {})
        services.jobs.wait(first, timeout=10)
        second = services.jobs.submit(JobKind.ORG_TYPE, {"dry_run": True})
        services.jobs.wait(second, timeout=10)
        assert next(record.id for record in services.jobs.jobs(limit=10)) == second

    def test_running_count_drops_after_finish(self, services: Services) -> None:
        job_id = services.jobs.submit(JobKind.SUMMARIZE, {})
        services.jobs.wait(job_id, timeout=10)
        assert services.jobs.running_count() == 0
        assert not services.jobs.is_kind_running(JobKind.SUMMARIZE)

    def test_manager_requires_known_kind(self, services: Services) -> None:
        with pytest.raises(JobError):
            services.jobs.submit("nope", {})

    def test_cancel_finalizes_orphan_record(self, services: Services) -> None:
        from regwatch.domain import TaskRecord

        # 进程重启后遗留的「执行中」记录：无本进程线程，取消时直接落库收尾
        services.store.tasks.save(
            TaskRecord(
                id="orphan",
                kind="fetch_amac",
                title="AMAC 案例抓取",
                status=JobStatus.RUNNING,
                total=15,
                processed=1,
            )
        )
        assert services.jobs.cancel("orphan") is True
        record = services.jobs.get("orphan")
        assert record is not None
        assert record.status is JobStatus.CANCELLED

    def test_cancel_returns_false_for_unknown_or_finished(self, services: Services) -> None:
        assert services.jobs.cancel("missing") is False
        job_id = services.jobs.submit(JobKind.SUMMARIZE, {})
        services.jobs.wait(job_id, timeout=10)
        assert services.jobs.cancel(job_id) is False

    def test_init_recovers_stale_running_tasks(self, store: DataStore, services: Services) -> None:
        from regwatch.domain import TaskRecord

        for task_id, status in (
            ("stale-run", JobStatus.RUNNING),
            ("stale-pending", JobStatus.PENDING),
        ):
            store.tasks.save(TaskRecord(id=task_id, kind="fetch_amac", status=status))
        JobManager(store.tasks, JobRunner(services))  # 重建管理器触发启动恢复

        for task_id in ("stale-run", "stale-pending"):
            record = store.tasks.get(task_id)
            assert record is not None
            assert record.status is JobStatus.FAILED
            assert record.message == "进程重启，任务已中断"
        assert not services.jobs.is_kind_running(JobKind.FETCH_AMAC)


def test_job_manager_can_be_recreated(store: DataStore, services: Services) -> None:
    other = JobManager(store.tasks, JobRunner(services))
    job_id = other.submit(JobKind.SUMMARIZE, {})
    assert other.wait(job_id, timeout=10) is not None
