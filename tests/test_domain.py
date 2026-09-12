"""领域层测试：枚举解析与数据模型的容错能力。"""

from __future__ import annotations

from regwatch.domain import (
    CaseQuery,
    CaseRecord,
    CaseRow,
    CaseStatus,
    CaseType,
    Category,
    Dataset,
    JobKind,
    JobStatus,
    ModelProfile,
    SummaryRecord,
    TaskRecord,
    split_multi_value,
)
from regwatch.domain.violations import (
    VIOLATION_TYPES,
    categorize_punishment,
    normalize_violations,
)


class TestEnums:
    def test_dataset_parse_tolerates_unknown(self) -> None:
        assert Dataset.parse("amac") is Dataset.AMAC
        assert Dataset.parse("nope") is None
        assert Dataset.parse_many("all") == Dataset.all()
        assert Dataset.parse_many("amac,csrc") == (Dataset.AMAC, Dataset.CSRC)

    def test_case_status_labels(self) -> None:
        assert CaseStatus.DONE.label == "已提取"
        assert CaseStatus.SKIPPED.is_finished
        assert not CaseStatus.PENDING.is_finished

    def test_case_type_parse_many(self) -> None:
        assert CaseType.parse_many("penalty") == (CaseType.PENALTY,)
        assert len(CaseType.parse_many("all")) == 2

    def test_job_enums(self) -> None:
        assert JobKind.parse("summarize") is JobKind.SUMMARIZE
        assert JobStatus.SUCCESS.is_finished
        assert not JobStatus.RUNNING.is_finished


class TestModels:
    def test_case_record_normalizes_enums_from_strings(self) -> None:
        case = CaseRecord(dataset="csrc", case_id="x", status="done", case_type="penalty")
        assert case.dataset is Dataset.CSRC
        assert case.status is CaseStatus.DONE
        assert case.case_type is CaseType.PENALTY

    def test_case_record_ignores_unknown_fields(self) -> None:
        case = CaseRecord.from_dict({"case_id": "1", "unknown": "x"})
        assert case.case_id == "1"

    def test_is_fund_related_is_tri_state(self) -> None:
        assert CaseRecord.from_dict({"is_fund_related": None}).is_fund_related is None
        assert CaseRecord.from_dict({"is_fund_related": "true"}).is_fund_related is True
        assert CaseRecord.from_dict({"is_fund_related": ""}).is_fund_related is None

    def test_summary_splits_violations(self) -> None:
        summary = SummaryRecord(violation_type="违规募集、内控缺失")
        assert summary.violation_types == ("违规募集", "内控缺失")

    def test_case_row_to_dict_exposes_display_labels(self) -> None:
        """回归：网页端按 dict 键取展示文案，to_dict 必须带上派生标签。"""
        row = CaseRow(dataset=Dataset.CSRC, case_id="x", status=CaseStatus.DONE, title="标题")
        data = row.to_dict()
        assert data["dataset"] == "csrc" and data["status"] == "done"
        assert data["dataset_label"] == Dataset.CSRC.label
        assert data["status_label"] == CaseStatus.DONE.label

    def test_task_record_from_dict_does_not_crash(self) -> None:
        """回归：旧版 TaskRecord.from_dict 会因未继承基类而抛 AttributeError。"""
        record = TaskRecord.from_dict(
            {"id": "1", "kind": "summarize", "status": "success", "processed": 3, "total": 10}
        )
        assert record.status is JobStatus.SUCCESS
        assert round(record.progress, 2) == 0.3
        assert record.progress_percent == 30
        assert "progress_percent" in record.to_dict()

    def test_task_record_normalizes_bad_status(self) -> None:
        assert TaskRecord.from_dict({"status": "weird"}).status is JobStatus.PENDING

    def test_query_replace_keeps_other_fields(self) -> None:
        query = CaseQuery(datasets=(Dataset.AMAC,))
        updated = query.replace(keyword="基金")
        assert updated.keyword == "基金"
        assert updated.datasets == (Dataset.AMAC,)
        assert query.keyword == ""

    def test_model_profile_masked_key(self) -> None:
        profile = ModelProfile(id="a", api_key="sk-1234567890abcdef")
        assert profile.masked_key.startswith("sk-1")
        assert "1234567890abcdef" not in profile.masked_key
        assert ModelProfile(id="a").missing_fields() == ["接口地址 base_url", "文本模型 model"]


class TestViolations:
    def test_normalize_keeps_unknown_type(self) -> None:
        assert normalize_violations("违规募集、全新类型") == ["违规募集", "全新类型"]

    def test_normalize_empty_becomes_unclassified(self) -> None:
        assert normalize_violations("") == ["未分类"]

    def test_categorize_punishment(self) -> None:
        assert categorize_punishment("处以罚款 100 万元") == "罚款"
        assert categorize_punishment("出具警示函") == "出具警示函"
        assert categorize_punishment("") == "其他"

    def test_classification_system_unchanged(self) -> None:
        """分类体系顺序与内容保持稳定（改动会影响历史报告可比性）。"""
        assert VIOLATION_TYPES[0] == "信息披露违规"
        assert "其他" in VIOLATION_TYPES
        assert len(set(VIOLATION_TYPES)) == len(VIOLATION_TYPES)


class TestHelpers:
    def test_split_multi_value(self) -> None:
        assert split_multi_value("a、b，c/ d") == ("a", "b", "c", "d")
        assert split_multi_value("") == ()

    def test_category_label(self) -> None:
        assert Category.INSTITUTION.label == "机构"
        assert CaseRecord(category=Category.PERSONNEL).entity_kind == "个人"
