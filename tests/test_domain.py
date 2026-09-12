"""领域层测试：枚举解析、数据模型容错与违规类型分类体系。"""

from __future__ import annotations

import pytest

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
    VIOLATION_TYPES_AMAC,
    VIOLATION_TYPES_CSRC,
    categorize_punishment,
    normalize_violations,
    violation_candidates,
    violation_label,
    violations_text,
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

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("适当性管理不到位", "违规募集"),
            ("未做适当性管理", "违规募集"),
            ("承诺保本保收益", "违规募集"),
            ("未及时更新备案信息", "未按规定备案"),
            ("虚假记载", "信息披露违规"),
            ("未设托管机构", "未按规定托管"),
            ("操纵证券价格", "操纵市场"),
            ("泄露内幕信息", "内幕交易"),
            ("兼营与私募基金管理无关的业务", "非专业化运营"),
            ("尽调不充分", "未尽勤勉尽责义务"),
            ("办公场所不独立或不合规", "人员与场所违规"),
            ("拒不配合协会检查", "未配合自律管理"),
        ],
    )
    def test_normalize_alias_to_canonical(self, raw: str, expected: str) -> None:
        """提示词里的子项 / 别名表述必须归一到 canonical 类型。"""
        assert normalize_violations(raw) == [expected]

    def test_normalize_merges_cross_dataset_synonyms(self) -> None:
        """CSRC 与 AMAC 的同义措辞归一到同一 canonical，跨数据集统计不再分裂。"""
        assert normalize_violations("未勤勉尽责") == ["未尽勤勉尽责义务"]
        assert normalize_violations("未配合监管") == ["未配合自律管理"]

    def test_normalize_splits_colon_and_middle_dot(self) -> None:
        assert normalize_violations("违规募集：向不合格投资者募集") == ["违规募集"]
        assert normalize_violations("未配合自律管理·未持续符合登记条件·未按规定备案") == [
            "未配合自律管理",
            "未持续符合登记条件",
            "未按规定备案",
        ]

    def test_other_requires_exact_match(self) -> None:
        """「其他」是短词，只能精确匹配，避免「其他情形」被误判。"""
        assert normalize_violations("其他") == ["其他"]
        assert normalize_violations("其他情形") == ["其他情形"]

    def test_candidates_follow_dataset_scope(self) -> None:
        assert violation_candidates("amac") == VIOLATION_TYPES_AMAC
        assert violation_candidates("csrc") == VIOLATION_TYPES_CSRC
        assert "操纵市场" not in VIOLATION_TYPES_AMAC
        assert "内幕交易" not in VIOLATION_TYPES_AMAC
        # CSRC 侧补齐私募特有类型，避免案例因提示词缺项而漏采
        for name in ("未按规定估值", "非专业化运营", "人员与场所违规", "未持续符合登记条件"):
            assert name in VIOLATION_TYPES_CSRC

    def test_label_by_dataset(self) -> None:
        assert violation_label("未尽勤勉尽责义务", Dataset.CSRC) == "未勤勉尽责"
        assert violation_label("未尽勤勉尽责义务", Dataset.AMAC) == "未尽勤勉尽责义务"
        # 别名同样可以映射到展示名
        assert violation_label("未配合监管", Dataset.AMAC) == "未配合自律管理"

    def test_violations_text_normalizes_and_labels(self) -> None:
        assert (
            violations_text("适当性管理不到位、未勤勉尽责", Dataset.CSRC) == "违规募集、未勤勉尽责"
        )
        assert violations_text("") == ""


class TestPromptCoverage:
    """提取提示词的候选类型由分类体系派生，两者不得脱节。"""

    def test_extract_prompts_cover_candidates(self) -> None:
        from regwatch.prompts import AMAC_EXTRACT_PROMPT, CSRC_EXTRACT_PROMPT

        for name in VIOLATION_TYPES_AMAC:
            assert name in AMAC_EXTRACT_PROMPT, f"AMAC 提示词缺少候选类型：{name}"
        for name in VIOLATION_TYPES_CSRC:
            assert name in CSRC_EXTRACT_PROMPT, f"CSRC 提示词缺少候选类型：{name}"

    def test_qa_intent_prompt_lists_all_types(self) -> None:
        from regwatch.prompts import QA_INTENT_PROMPT

        for name in VIOLATION_TYPES:
            assert name in QA_INTENT_PROMPT


class TestHelpers:
    def test_split_multi_value(self) -> None:
        assert split_multi_value("a、b，c/ d") == ("a", "b", "c", "d")
        assert split_multi_value("") == ()

    def test_category_label(self) -> None:
        assert Category.INSTITUTION.label == "机构"
        assert CaseRecord(category=Category.PERSONNEL).entity_kind == "个人"
