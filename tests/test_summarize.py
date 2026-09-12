"""结构化摘要提取服务测试（模型由假实现替换，不触网）。"""

from __future__ import annotations

from typing import Any

import pytest

from regwatch.db import DataStore
from regwatch.domain import CaseQuery, CaseRecord, CaseStatus, Dataset
from regwatch.services import SummarizationService

AMAC_PAYLOAD = {
    "punished_entity": "某某基金管理有限公司",
    "entity_type": "机构",
    "violation_type": "违规募集、内控缺失",
    "punishment": "公开谴责",
    "punishment_date": "2026-01-05",
    "involved_fund": "某某私募基金",
    "violation_summary": "该公司向不合格投资者募集资金，内控制度不健全。",
    "legal_basis": "《私募投资基金监督管理暂行办法》第三十八条",
}


@pytest.fixture
def service(store: DataStore, fake_llm: Any) -> SummarizationService:
    return SummarizationService(
        store.cases, store.summaries, fake_llm, default_concurrency=1, max_retries=1
    )


class TestSummarizeOne:
    def test_amac_success(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        service._llm.payload = AMAC_PAYLOAD  # type: ignore[union-attr]
        store.cases.upsert(amac_case)

        status = service.summarize_one(store.cases.get_with_body(Dataset.AMAC, amac_case.case_id))
        assert status is CaseStatus.DONE

        case = store.cases.get(Dataset.AMAC, amac_case.case_id)
        assert case.status is CaseStatus.DONE

        summary = store.summaries.get(Dataset.AMAC, amac_case.case_id)
        assert summary is not None and summary.extract_success
        assert summary.violation_types == ("违规募集", "内控缺失")
        assert summary.punishment == "公开谴责"
        assert store.summaries.violations_of(Dataset.AMAC, amac_case.case_id) == (
            "内控缺失",
            "违规募集",
        )

    def test_csrc_skipped_when_not_fund_related(
        self, store: DataStore, service: SummarizationService, csrc_case: CaseRecord
    ) -> None:
        service._llm.payload = {  # type: ignore[union-attr]
            "fund_related": False,
            "fund_relation_reason": "当事人为证券公司，违规业务与基金无关",
        }
        store.cases.upsert(csrc_case)

        status = service.summarize_one(store.cases.get_with_body(Dataset.CSRC, csrc_case.case_id))
        assert status is CaseStatus.SKIPPED
        assert store.summaries.get(Dataset.CSRC, csrc_case.case_id) is None
        case = store.cases.get(Dataset.CSRC, csrc_case.case_id)
        assert case.status is CaseStatus.SKIPPED
        assert "证券公司" in case.status_note

    def test_csrc_success_records_penalty(
        self, store: DataStore, service: SummarizationService, csrc_case: CaseRecord
    ) -> None:
        service._llm.payload = {  # type: ignore[union-attr]
            "fund_related": True,
            "entity_type": "机构",
            "violation_type": "信息披露违规",
            "punishment": "罚款",
            "penalty_amount": "100",
            "legal_basis": "《证券投资基金法》第一百三十三条",
        }
        store.cases.upsert(csrc_case)
        service.summarize_one(store.cases.get_with_body(Dataset.CSRC, csrc_case.case_id))

        summary = store.summaries.get(Dataset.CSRC, csrc_case.case_id)
        assert summary is not None and summary.penalty_amount == "100"

    def test_short_text_fails(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        store.cases.upsert(amac_case.replace(raw_text="太短"))
        status = service.summarize_one(store.cases.get_with_body(Dataset.AMAC, amac_case.case_id))
        assert status is CaseStatus.FAILED
        assert store.cases.get(Dataset.AMAC, amac_case.case_id).status_note

    def test_unparsable_response_fails(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        service._llm.payload = "这不是 JSON"  # type: ignore[union-attr]
        store.cases.upsert(amac_case)
        status = service.summarize_one(store.cases.get_with_body(Dataset.AMAC, amac_case.case_id))
        assert status is CaseStatus.FAILED


class TestSummarizeBatch:
    def test_counts_and_skips_finished(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        service._llm.payload = AMAC_PAYLOAD  # type: ignore[union-attr]
        store.cases.upsert(amac_case)
        store.cases.upsert(amac_case.replace(case_id="P2", title="另一案例"))

        result = service.summarize(Dataset.AMAC)
        assert result.total == 2
        assert result.success == 2
        assert result.failed == 0

        # 已完成的不重复处理
        again = service.summarize(Dataset.AMAC)
        assert again.total == 0

    def test_include_done_reruns_finished_cases(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        """提示词 / 分类体系升级后的回填入口：include_done 会重跑已完成案例。"""
        service._llm.payload = AMAC_PAYLOAD  # type: ignore[union-attr]
        store.cases.upsert(amac_case)
        assert service.summarize(Dataset.AMAC).success == 1

        rerun = service.summarize(Dataset.AMAC, include_done=True)
        assert rerun.total == 1
        assert rerun.success == 1
        assert service.candidates(Dataset.AMAC, include_done=True)

    def test_limit_is_respected(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        service._llm.payload = AMAC_PAYLOAD  # type: ignore[union-attr]
        store.cases.upsert(amac_case)
        store.cases.upsert(amac_case.replace(case_id="P2"))
        assert service.summarize(Dataset.AMAC, limit=1).total == 1

    def test_progress_hook_reports_each_case(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        service._llm.payload = AMAC_PAYLOAD  # type: ignore[union-attr]
        store.cases.upsert(amac_case)
        seen: list[tuple[int, int, str]] = []
        service.summarize(
            Dataset.AMAC, on_progress=lambda done, total, label: seen.append((done, total, label))
        )
        assert seen == [(1, 1, amac_case.case_id)]

    def test_unknown_dataset_raises(self, service: SummarizationService) -> None:
        with pytest.raises(ValueError):
            service.summarize("nope")

    def test_data_changed_callback_is_invoked(
        self, store: DataStore, fake_llm: Any, amac_case: CaseRecord
    ) -> None:
        calls: list[int] = []
        service = SummarizationService(
            store.cases,
            store.summaries,
            fake_llm,
            default_concurrency=1,
            max_retries=1,
            on_data_changed=lambda: calls.append(1),
        )
        fake_llm.payload = AMAC_PAYLOAD
        store.cases.upsert(amac_case)
        service.summarize(Dataset.AMAC)
        assert calls

    def test_candidates_exclude_finished(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        store.cases.upsert(amac_case)
        store.cases.set_status(Dataset.AMAC, amac_case.case_id, CaseStatus.DONE)
        assert service.candidates(Dataset.AMAC) == []

    def test_query_rows_after_summarize(
        self, store: DataStore, service: SummarizationService, amac_case: CaseRecord
    ) -> None:
        service._llm.payload = AMAC_PAYLOAD  # type: ignore[union-attr]
        store.cases.upsert(amac_case)
        service.summarize(Dataset.AMAC)
        rows = store.cases.search(CaseQuery(statuses=(CaseStatus.DONE,)))
        assert len(rows) == 1
        assert rows[0].violation_types == ("违规募集", "内控缺失")
