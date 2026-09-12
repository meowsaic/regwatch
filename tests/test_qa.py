"""智能问答：意图模型、证据包、QaService 管线。"""

from __future__ import annotations

import json
from typing import Any

from regwatch.domain import CaseRecord, CaseRow, CaseStatus, Dataset, QaIntent
from regwatch.domain.models import CaseQuery
from regwatch.services import AnalysisService, QaService, build_evidence_pack
from regwatch.services.qa import QaRetrieval


class SequenceClient:
    """按调用次序返回预设文本的假客户端。"""

    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def chat_text(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "system": system, **kwargs})
        if not self.payloads:
            raise AssertionError("SequenceClient 用尽")
        return self.payloads.pop(0)

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class TestQaIntent:
    def test_from_llm_dict_normalizes(self) -> None:
        intent = QaIntent.from_llm_dict(
            {
                "mode": "report",
                "keywords": ["挪用", " 挪用 ", "", "基金财产"],
                "datasets": ["AMAC", "csrc", "nope"],
                "violations": ["挪用基金财产"],
                "date_from": "2025-06-01",
                "date_to": "2024-01-01",
                "limit": 999,
                "fund_related_only": "yes",
                "notes": " ok ",
            }
        )
        assert intent.mode == "report"
        assert intent.keywords == ("挪用", "基金财产")
        assert intent.datasets == (Dataset.AMAC, Dataset.CSRC)
        assert intent.violations == ("挪用基金财产",)
        assert intent.limit == 40
        assert intent.fund_related_only is True
        assert intent.notes == "ok"
        assert intent.date_from == "2024-01-01"
        assert intent.date_to == "2025-06-01"

    def test_from_llm_dict_empty(self) -> None:
        intent = QaIntent.from_llm_dict(None)
        assert intent.mode == "qa"
        assert intent.keywords == ()
        assert intent.limit == 15

    def test_to_case_query_maps_fields(self) -> None:
        intent = QaIntent(
            keywords=("挪用", "基金"),
            violations=("挪用基金财产",),
            datasets=(Dataset.AMAC,),
            date_from="2024-01-01",
            limit=10,
        )
        query = intent.to_case_query(keyword="基金")
        assert isinstance(query, CaseQuery)
        assert query.keyword == "基金"
        assert query.violations == ("挪用基金财产",)
        assert query.datasets == (Dataset.AMAC,)
        assert query.limit == 10
        assert query.statuses == (CaseStatus.DONE,)

    def test_swap_inverted_dates(self) -> None:
        intent = QaIntent(date_from="2025-06-01", date_to="2024-01-01")
        assert intent.date_from == "2024-01-01"
        assert intent.date_to == "2025-06-01"

    def test_invalid_dates_dropped(self) -> None:
        intent = QaIntent(date_from="2025-13-99", date_to="not-a-date")
        assert intent.date_from is None
        assert intent.date_to is None

    def test_unknown_violation_demoted_to_keyword(self) -> None:
        intent = QaIntent.from_llm_dict({"violations": ["挪用基金财产", "某类未收录违规"]})
        assert intent.violations == ("挪用基金财产",)
        assert "某类未收录违规" in intent.keywords

    def test_normalize_containing_violation_phrase(self) -> None:
        intent = QaIntent.from_llm_dict({"violations": ["违规募集（向不合格投资者募集）"]})
        assert intent.violations == ("违规募集",)


class TestEvidencePack:
    def test_empty_rows(self) -> None:
        text = build_evidence_pack([], {"total": 0})
        assert "无命中案例" in text

    def test_includes_case_fields(self, amac_case: CaseRecord) -> None:
        row = CaseRow(
            dataset=amac_case.dataset,
            case_id=amac_case.case_id,
            title=amac_case.title,
            date=amac_case.date,
            punished_entities=amac_case.punished_entity,
            violation_summary="存在违规募集事实",
            punishment="公开谴责",
            source_url="https://example.com/x",
        )
        text = build_evidence_pack([row], {"total": 1, "violations": [("违规募集", 1)]})
        assert "【案例1】" in text
        assert amac_case.case_id in text
        assert "公开谴责" in text
        assert "违规募集" in text


class TestQaServicePipeline:
    def test_intent_json_failure_falls_back(self, store, amac_case, csrc_case) -> None:
        store.cases.upsert(amac_case)
        store.cases.upsert(csrc_case)
        analysis = AnalysisService(store.cases, store.summaries)
        client = SequenceClient(["这不是 JSON", "答案内容"])
        service = QaService(None, analysis)
        intent, fallback, _raw = service.parse_intent("挪用基金财产", client=client)
        assert fallback is True
        assert intent.keywords

    def test_full_qa_flow(self, store, amac_case, csrc_case) -> None:
        store.cases.upsert(amac_case)
        store.cases.upsert(csrc_case)
        analysis = AnalysisService(store.cases, store.summaries)
        intent_payload = json.dumps(
            {
                "mode": "qa",
                "keywords": ["某某"],
                "datasets": ["amac", "csrc"],
                "limit": 10,
                "notes": "当事人关键词",
            },
            ensure_ascii=False,
        )
        client = SequenceClient([intent_payload, "根据证据，某某基金存在违规【案例1】。"])
        service = QaService(None, analysis)
        turn = service.ask("某某基金有哪些处分？", client=client)
        assert turn.mode == "qa"
        assert turn.used_fallback_intent is False
        assert "案例" in turn.answer
        assert turn.retrieval.rows
        assert any(row.case_id == amac_case.case_id for row in turn.retrieval.rows)
        assert len(client.calls) == 2

    def test_report_mode_forced(self, store, amac_case) -> None:
        store.cases.upsert(amac_case)
        analysis = AnalysisService(store.cases, store.summaries)
        intent_payload = json.dumps({"mode": "qa", "keywords": ["某某"]}, ensure_ascii=False)
        client = SequenceClient([intent_payload, "# 专题报告\n内容"])
        service = QaService(None, analysis)
        turn = service.ask("写报告", mode="report", client=client)
        assert turn.mode == "report"
        assert turn.answer.startswith("#")

    def test_zero_hits_still_answers(self, store) -> None:
        analysis = AnalysisService(store.cases, store.summaries)
        intent_payload = json.dumps(
            {"mode": "qa", "keywords": ["绝无此当事人xyz"], "limit": 5}, ensure_ascii=False
        )
        client = SequenceClient([intent_payload, "当前案例库证据不足。"])
        service = QaService(None, analysis)
        turn = service.ask("绝无此当事人xyz有哪些案例", client=client)
        assert turn.retrieval.rows == ()
        assert "证据不足" in turn.answer

    def test_retrieve_merges_keywords(self, store, amac_case, csrc_case) -> None:
        store.cases.upsert(amac_case)
        store.cases.upsert(csrc_case)
        analysis = AnalysisService(store.cases, store.summaries)
        service = QaService(None, analysis)
        intent = QaIntent(keywords=("某某基金", "某某证券"), limit=10)
        retrieval = service.retrieve(intent)
        assert isinstance(retrieval, QaRetrieval)
        assert len(retrieval.rows) >= 2
        ids = {(r.dataset, r.case_id) for r in retrieval.rows}
        assert (Dataset.AMAC, amac_case.case_id) in ids
        assert (Dataset.CSRC, csrc_case.case_id) in ids
