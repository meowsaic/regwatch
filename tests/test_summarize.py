"""摘要提取与任务编排的纯本地测试（使用假模型，不发网络请求）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from regwatch.config import Config
from regwatch.datamodels import ModelProfile, TaskRecord, TaskStatus
from regwatch.jobs import JOB_KINDS, JOB_LABELS, JobError, JobManager, run_task
from regwatch.llm import ChatResult, LLMError
from regwatch.storage import (
    DATASET_AMAC,
    DATASET_CSRC,
    SummaryIndex,
    read_json,
    write_json,
)
from regwatch.summarize import (
    CaseRef,
    extract_structured,
    scan_candidates,
    summarize,
    summarize_one,
)

AMAC_PAYLOAD = json.dumps(
    {
        "punished_entity": "某投资管理有限公司",
        "entity_type": "机构",
        "violation_type": "内控缺失、未尽勤勉尽责义务",
        "punishment": "公开谴责",
        "punishment_date": "2025-01-01",
        "involved_fund": "某私募基金",
        "violation_summary": "内控制度形同虚设，合规风控人员兼任冲突职务。",
        "legal_basis": "《私募投资基金监督管理暂行办法》第四条",
    },
    ensure_ascii=False,
)

CSRC_PAYLOAD = json.dumps(
    {
        "entity_type": "机构",
        "violation_type": "内控缺失",
        "punishment": "出具警示函",
        "involved_fund": "",
        "violation_summary": "内部控制不健全。",
        "legal_basis": "《私募投资基金监督管理暂行办法》第四条",
        "penalty_amount": "",
        "market_ban": "",
        "fund_related": True,
        "fund_relation_reason": "当事人为私募基金管理人",
    },
    ensure_ascii=False,
)


class FakeLLM:
    """按脚本依次返回内容的假模型客户端；脚本耗尽后重复最后一项。"""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list = []
        self.profile = ModelProfile(
            id="fake",
            label="假模型",
            base_url="https://fake.invalid",
            model="fake-model",
        )

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.responses:
            item = self.responses.pop(0)
            self._last = item
        else:
            item = getattr(self, "_last", None)
            if item is None:
                item = AMAC_PAYLOAD
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(messages)
        return ChatResult(content=str(item))


class TempConfig(Config):
    """数据根目录指向临时目录的配置。"""

    def __init__(self, base: Path) -> None:
        super().__init__(path=base / "config.json")
        self.data["data_roots"] = {
            "amac_cases": str(base / "amac" / "cases"),
            "amac_summaries": str(base / "amac" / "summaries"),
            "amac_reports": str(base / "amac" / "reports"),
            "csrc_cases": str(base / "csrc" / "cases"),
            "csrc_summaries": str(base / "csrc" / "summaries"),
            "csrc_reports": str(base / "csrc" / "reports"),
        }


def write_case(cfg: TempConfig, dataset: str, rel_path: str, payload: dict) -> Path:
    root = cfg.data_root("amac_cases" if dataset == DATASET_AMAC else "csrc_cases")
    path = root / rel_path
    write_json(path, payload)
    return path


def amac_case_payload(case_id: str, text: str = "正文" * 200) -> dict:
    return {
        "case_id": case_id,
        "source_url": "https://amac.example/x",
        "category": "scfjg",
        "title": "关于对某投资管理有限公司的纪律处分决定书",
        "date": "2025-01-01",
        "raw_text": text,
        "punished_entity": "某投资管理有限公司",
        "org_type": "私募证券投资基金管理人",
        "ocr_success": True,
    }


class SummarizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.cfg = TempConfig(self.base)

    def test_scan_candidates_respects_index(self):
        write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1001.json",
            amac_case_payload("20250101_1001"),
        )
        write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1002.json",
            amac_case_payload("20250101_1002"),
        )
        index = SummaryIndex(self.cfg.data_root("amac_summaries"))
        index.mark_done("20250101_1001", "20250101_1001_summary.json")

        refs = scan_candidates(DATASET_AMAC, self.cfg)
        self.assertEqual([ref.case_id for ref in refs], ["20250101_1002"])

        index.mark_skipped("20250101_1002", "非基金相关")
        self.assertEqual(scan_candidates(DATASET_AMAC, self.cfg), [])

        refs = scan_candidates(DATASET_AMAC, self.cfg, retry_failed=False, limit=1)
        self.assertEqual(refs, [])

    def test_scan_candidates_csrc_layout(self):
        write_case(
            self.cfg,
            DATASET_CSRC,
            "Beijing/measure/20250301_c1234567.json",
            {
                "case_id": "20250301_c1234567",
                "raw_text": "正文" * 200,
                "bureau": "Beijing",
                "case_type": "measure",
            },
        )
        refs = scan_candidates(DATASET_CSRC, self.cfg)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].bureau, "Beijing")
        self.assertEqual(refs[0].case_type, "measure")
        self.assertEqual(
            refs[0].summary_relative_path,
            "Beijing/measure/20250301_c1234567_summary.json",
        )

    def test_extract_structured_returns_error_message(self):
        with patch("regwatch.summarize.time.sleep"):
            payload, error = extract_structured(
                "正文" * 100, DATASET_AMAC, client=FakeLLM([AMAC_PAYLOAD])
            )
            self.assertIsNotNone(payload)
            self.assertEqual(error, "")

            payload, error = extract_structured(
                "正文" * 100, DATASET_AMAC, client=FakeLLM([LLMError("密钥无效")])
            )
            self.assertIsNone(payload)
            self.assertIn("密钥无效", error)

            payload, error = extract_structured("太短", DATASET_AMAC, client=FakeLLM([]))
            self.assertIsNone(payload)
            self.assertIn("过短", error)

    def test_summarize_one_amac_writes_flat_summary(self):
        path = write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1001.json",
            amac_case_payload("20250101_1001"),
        )
        ref = CaseRef(dataset=DATASET_AMAC, case_id="20250101_1001", path=path, category="scfjg")
        client = FakeLLM([AMAC_PAYLOAD])

        status = summarize_one(ref, config=self.cfg, client=client)
        self.assertEqual(status, "done")

        summary_path = self.cfg.data_root("amac_summaries") / "20250101_1001_summary.json"
        summary = read_json(summary_path)
        self.assertTrue(summary["extract_success"])
        self.assertEqual(summary["entity_type"], "机构")
        self.assertEqual(summary["violation_type"], "内控缺失、未尽勤勉尽责义务")
        self.assertEqual(summary["punished_entity"], "某投资管理有限公司")
        self.assertEqual(summary["org_type"], "私募证券投资基金管理人")

        index = SummaryIndex(self.cfg.data_root("amac_summaries"))
        self.assertTrue(index.is_done("20250101_1001"))
        self.assertEqual(index.info("20250101_1001")["summary_file"], "20250101_1001_summary.json")

    def test_summarize_one_csrc_writes_nested_summary(self):
        path = write_case(
            self.cfg,
            DATASET_CSRC,
            "Beijing/measure/20250301_c1234567.json",
            {
                "case_id": "20250301_c1234567",
                "raw_text": "正文" * 200,
                "bureau": "Beijing",
                "case_type": "measure",
                "date": "2025-03-01",
                "punished_entities": "某基金管理有限公司",
            },
        )
        ref = CaseRef(
            dataset=DATASET_CSRC,
            case_id="20250301_c1234567",
            path=path,
            bureau="Beijing",
            case_type="measure",
        )
        status = summarize_one(ref, config=self.cfg, client=FakeLLM([CSRC_PAYLOAD]))
        self.assertEqual(status, "done")

        summary_path = (
            self.cfg.data_root("csrc_summaries")
            / "Beijing"
            / "measure"
            / "20250301_c1234567_summary.json"
        )
        summary = read_json(summary_path)
        self.assertTrue(summary["extract_success"])
        self.assertEqual(summary["bureau"], "Beijing")
        self.assertEqual(summary["llm_provider"], "fake")

    def test_summarize_one_csrc_skips_non_fund(self):
        payload = json.loads(CSRC_PAYLOAD)
        payload["fund_related"] = False
        payload["fund_relation_reason"] = "当事人为证券公司营业部"
        path = write_case(
            self.cfg,
            DATASET_CSRC,
            "HQ/measure/20250101_c9999999.json",
            {
                "case_id": "20250101_c9999999",
                "raw_text": "正文" * 200,
                "bureau": "HQ",
                "case_type": "measure",
            },
        )
        ref = CaseRef(
            dataset=DATASET_CSRC,
            case_id="20250101_c9999999",
            path=path,
            bureau="HQ",
            case_type="measure",
        )
        status = summarize_one(
            ref, config=self.cfg, client=FakeLLM([json.dumps(payload, ensure_ascii=False)])
        )
        self.assertEqual(status, "skipped")
        # 跳过的案例不应生成摘要文件
        self.assertFalse(
            (
                self.cfg.data_root("csrc_summaries")
                / "HQ"
                / "measure"
                / "20250101_c9999999_summary.json"
            ).exists()
        )
        index = SummaryIndex(self.cfg.data_root("csrc_summaries"))
        self.assertEqual(index.status_of("20250101_c9999999"), "skipped")
        self.assertIn("证券公司营业部", index.info("20250101_c9999999")["reason"])

    def test_summarize_one_records_real_error(self):
        path = write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1003.json",
            amac_case_payload("20250101_1003"),
        )
        ref = CaseRef(dataset=DATASET_AMAC, case_id="20250101_1003", path=path, category="scfjg")
        with patch("regwatch.summarize.time.sleep"):
            status = summarize_one(
                ref, config=self.cfg, client=FakeLLM([LLMError("未配置 API Key")])
            )
        self.assertEqual(status, "failed")
        index = SummaryIndex(self.cfg.data_root("amac_summaries"))
        self.assertIn("未配置 API Key", index.info("20250101_1003")["error"])

    def test_summarize_one_rejects_short_text(self):
        path = write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1004.json",
            amac_case_payload("20250101_1004", text="太短"),
        )
        ref = CaseRef(dataset=DATASET_AMAC, case_id="20250101_1004", path=path, category="scfjg")
        status = summarize_one(ref, config=self.cfg, client=FakeLLM([AMAC_PAYLOAD]))
        self.assertEqual(status, "failed")

    def test_summarize_batch_with_progress(self):
        for index in range(2):
            write_case(
                self.cfg,
                DATASET_AMAC,
                f"institution/2025010{index + 1}_200{index}.json",
                amac_case_payload(f"2025010{index + 1}_200{index}"),
            )
        seen: list = []
        with (
            patch("regwatch.summarize.get_llm", return_value=FakeLLM([AMAC_PAYLOAD])),
            patch("regwatch.summarize.time.sleep"),
        ):
            result = summarize(
                DATASET_AMAC,
                config=self.cfg,
                workers=1,
                on_progress=lambda done, total, label: seen.append((done, total)),
            )
        self.assertEqual(result.total, 2)
        self.assertEqual(result.success, 2)
        self.assertEqual(seen, [(1, 2), (2, 2)])

        # 第二次运行应全部跳过（断点续传）
        again = summarize(DATASET_AMAC, config=self.cfg, workers=1)
        self.assertEqual((again.total, again.success), (0, 0))


class RunTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.cfg = TempConfig(self.base)
        # 一条已完成摘要的案例（供报告测试使用）
        write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1001.json",
            amac_case_payload("20250101_1001"),
        )
        write_json(
            self.cfg.data_root("amac_summaries") / "20250101_1001_summary.json",
            {
                "case_id": "20250101_1001",
                "category": "scfjg",
                "date": "2025-01-01",
                "punished_entity": "某投资管理有限公司",
                "entity_type": "机构",
                "violation_type": "内控缺失",
                "punishment": "公开谴责",
                "violation_summary": "内控不健全。",
                "extract_success": True,
            },
        )
        SummaryIndex(self.cfg.data_root("amac_summaries")).mark_done(
            "20250101_1001",
            "20250101_1001_summary.json",
        )
        # 一条尚未提取摘要的案例（供摘要任务测试使用）
        write_case(
            self.cfg,
            DATASET_AMAC,
            "institution/20250101_1002.json",
            amac_case_payload("20250101_1002"),
        )

    def test_unknown_kind_raises(self):
        with self.assertRaises(JobError):
            run_task("not-a-kind", {}, config=self.cfg)

    def test_summarize_task_via_run_task(self):
        with (
            patch("regwatch.summarize.get_llm", return_value=FakeLLM([AMAC_PAYLOAD])),
            patch("regwatch.summarize.time.sleep"),
        ):
            result = run_task("summarize", {"dataset": "amac", "workers": 1}, config=self.cfg)
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["total"], 1)

    def test_invalid_date_raises_job_error(self):
        with self.assertRaises(JobError):
            run_task("fetch_amac", {"start_date": "2025/01/01"}, config=self.cfg)

    def test_report_task_writes_files(self):
        result = run_task("report", {"dataset": "amac"}, config=self.cfg)
        paths = result["paths"]
        self.assertEqual(set(paths), {"md", "html", "json"})
        for path in paths.values():
            self.assertTrue(Path(path).exists())
        self.assertEqual(result["row_count"], 1)


class JobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = JobManager()

    def test_unknown_kind_rejected(self):
        with self.assertRaises(JobError):
            self.manager.submit("bogus", {})

    def _wait_status(
        self, job_id: str, status: TaskStatus, timeout: float = 3.0
    ) -> TaskRecord | None:
        deadline = __import__("time").monotonic() + timeout
        while __import__("time").monotonic() < deadline:
            record = self.manager.get(job_id)
            if record is not None and record.status is status:
                return record
            __import__("time").sleep(0.02)
        return self.manager.get(job_id)

    def test_same_kind_cannot_run_twice(self):
        release = __import__("threading").Event()

        def slow(kind, params, on_progress=None, config=None):
            release.wait(3)
            return {"total": 1, "success": 1}

        try:
            with patch("regwatch.jobs.run_task", side_effect=slow):
                job_id = self.manager.submit("summarize", {})
                self.assertIsNotNone(self._wait_status(job_id, TaskStatus.RUNNING))
                with self.assertRaises(JobError):
                    self.manager.submit("summarize", {})
        finally:
            release.set()
        record = self.manager.wait(job_id, timeout=5)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, TaskStatus.SUCCESS)
        self.assertEqual(record.result["success"], 1)

    def test_cancel_marks_running_job_cancelled(self):
        release = __import__("threading").Event()

        def slow(kind, params, on_progress=None, config=None):
            for index in range(50):
                if on_progress:
                    on_progress(index, 50, f"step-{index}")
                release.wait(0.05)
            return {"total": 50, "success": 50}

        try:
            with patch("regwatch.jobs.run_task", side_effect=slow):
                job_id = self.manager.submit("summarize", {})
                self.assertIsNotNone(self._wait_status(job_id, TaskStatus.RUNNING))
                self.assertTrue(self.manager.cancel(job_id))
        finally:
            release.set()
        record = self.manager.wait(job_id, timeout=5)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, TaskStatus.CANCELLED)

    def test_progress_updates_record(self):
        def runner(kind, params, on_progress=None, config=None):
            for index in range(1, 4):
                if on_progress:
                    on_progress(index, 3, f"step-{index}")
            return {"total": 3, "success": 3}

        with patch("regwatch.jobs.run_task", side_effect=runner):
            job_id = self.manager.submit("report", {})
            record = self.manager.wait(job_id, timeout=5)
        assert record is not None
        self.assertEqual(record.processed, 3)
        self.assertEqual(record.total, 3)
        self.assertEqual(record.progress_percent, 100)
        self.assertTrue(record.status.is_finished)

    def test_failure_recorded(self):
        def boom(kind, params, on_progress=None, config=None):
            raise RuntimeError("模拟失败")

        with patch("regwatch.jobs.run_task", side_effect=boom):
            job_id = self.manager.submit("org_type", {})
            record = self.manager.wait(job_id, timeout=5)
        assert record is not None
        self.assertEqual(record.status, TaskStatus.FAILED)
        self.assertIn("模拟失败", record.error)

    def test_job_kinds_documented(self):
        self.assertEqual(set(JOB_LABELS), set(JOB_KINDS))


if __name__ == "__main__":
    unittest.main()
