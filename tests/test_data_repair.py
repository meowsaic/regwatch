"""数据质量修复：抽取器与仓储回填。"""

from __future__ import annotations

from regwatch.db import DataStore
from regwatch.domain import CaseQuery, CaseRecord, CaseStatus, Dataset, SummaryRecord
from regwatch.services.data_repair import (
    DataRepairService,
    clean_csrc_entity,
    extract_document_number,
    extract_punished_entity_from_title,
    extract_punishment_date_from_text,
    is_valid_date,
    normalize_entity_type,
)


class TestExtractors:
    def test_title_bracket_person(self) -> None:
        assert extract_punished_entity_from_title("纪律处分决定书（朱峰）") == "朱峰"

    def test_title_rejects_doc_number_bracket(self) -> None:
        assert (
            extract_punished_entity_from_title(
                "中国证券监督管理委员会上海监管局行政处罚决定书沪〔2022〕19号"
            )
            == ""
        )

    def test_title_hyphen_review(self) -> None:
        assert extract_punished_entity_from_title("纪律处分复核决定书-葛长海") == "葛长海"

    def test_title_unclosed_bracket(self) -> None:
        assert (
            extract_punished_entity_from_title("纪律处分决定书（泓泉投资管理（平潭）有限公司")
            == "泓泉投资管理（平潭）有限公司"
        )

    def test_title_deregistration(self) -> None:
        assert (
            extract_punished_entity_from_title(
                "关于注销深圳市同盈股权基金管理有限公司私募基金管理人登记的公告"
            )
            == "深圳市同盈股权基金管理有限公司"
        )

    def test_clean_csrc_entity_strips_measure_suffix(self) -> None:
        assert clean_csrc_entity("张西湖采取出具警示函措施") == "张西湖"
        assert clean_csrc_entity("王欣采取出具警示函行政监管措施") == "王欣"
        assert clean_csrc_entity("某某资产管理有限公司采取出具警示函措施") == "某某资产管理有限公司"

    def test_is_valid_date(self) -> None:
        assert is_valid_date("2024-08-15")
        assert not is_valid_date("2024-08-74")
        assert not is_valid_date("2024-01-xx")
        assert not is_valid_date("1996-02-01")
        assert not is_valid_date("")

    def test_normalize_entity_type(self) -> None:
        assert normalize_entity_type("机构+个人") == "机构、个人"
        assert normalize_entity_type("机构") == "机构"
        assert normalize_entity_type("") == ""

    def test_csrc_entities_from_party_line(self) -> None:
        from regwatch.services.data_repair import extract_csrc_entities

        body = "〔2024〕130号\n当事人:马钰焰,女,1994年2月出生,住址:上海市杨浦区。"
        assert extract_csrc_entities("中国证券监督管理委员会行政处罚决定书", body) == "马钰焰"

    def test_csrc_person_title_not_polluted(self) -> None:
        from regwatch.services.data_repair import extract_csrc_entities

        entity = extract_csrc_entities("关于对张西湖采取出具警示函措施的决定", "")
        assert entity == "张西湖"
        assert "采取" not in entity

    def test_title_bracket_org(self) -> None:
        assert (
            extract_punished_entity_from_title("纪律处分决定书（深圳君创私募股权基金管理有限公司）")
            == "深圳君创私募股权基金管理有限公司"
        )

    def test_title_alt_bracket(self) -> None:
        assert (
            extract_punished_entity_from_title(
                "纪律处分复核决定书〔珠海通沛股权投资管理合伙企业（有限合伙）〕"
            )
            == "珠海通沛股权投资管理合伙企业（有限合伙）"
        )

    def test_title_multi_entity(self) -> None:
        assert (
            extract_punished_entity_from_title(
                "纪律处分决定书（中金信安投资基金（北京）有限公司、郑小龙、赵亚光）"
            )
            == "中金信安投资基金（北京）有限公司、郑小龙、赵亚光"
        )

    def test_doc_number_from_title(self) -> None:
        assert (
            extract_document_number(
                "关于对京智（广州）股权投资基金管理有限责任公司采取出具警示函措施的决定〔2023〕141号"
            )
            == "〔2023〕141号"
        )

    def test_doc_number_from_body(self) -> None:
        assert extract_document_number("关于对某某采取警示函措施的决定", "……〔2022〕2号……") == (
            "〔2022〕2号"
        )

    def test_punishment_date_arabic(self) -> None:
        text = "特此决定。\n中国证券投资基金业协会\n2026年5月11日"
        assert extract_punishment_date_from_text(text) == "2026-05-11"

    def test_punishment_date_chinese(self) -> None:
        text = "……二〇二六年五月十一日"
        assert extract_punishment_date_from_text(text) == "2026-05-11"


def _seed(store: DataStore) -> None:
    store.cases.upsert(
        CaseRecord(
            dataset=Dataset.AMAC,
            case_id="P1",
            title="纪律处分决定书（测试私募基金管理有限公司）",
            date="2026-08-21",
            status=CaseStatus.DONE,
            category="scfjg",
            raw_text="……协会决定作出如下纪律处分：公开谴责。\n二〇二六年五月十一日",
        )
    )
    store.cases.upsert(
        CaseRecord(
            dataset=Dataset.CSRC,
            case_id="C1",
            title="关于对某某资产管理有限公司采取出具警示函措施的决定〔2023〕9号",
            date="2023-01-01",
            status=CaseStatus.DONE,
            case_type="measure",
            bureau="Beijing",
            raw_text="当事人：某某资产管理有限公司。〔2023〕9号",
        )
    )
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.AMAC,
            case_id="P1",
            entity_type="机构",
            violation_type="违规募集",
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )
    store.summaries.upsert(
        SummaryRecord(
            dataset=Dataset.CSRC,
            case_id="C1",
            entity_type="机构",
            violation_type="信息披露违规",
            extract_success=True,
        ),
        status=CaseStatus.DONE,
    )


class TestRepairService:
    def test_dry_run_does_not_write(self, store: DataStore) -> None:
        _seed(store)
        report = DataRepairService(store).repair(dry_run=True)
        assert report.amac_entity_candidates == 1
        assert report.csrc_docnum_candidates == 1
        case = store.cases.get(Dataset.AMAC, "P1")
        assert case is not None
        assert case.punished_entity == ""

    def test_repair_fills_fields(self, store: DataStore) -> None:
        _seed(store)
        report = DataRepairService(store).repair(dry_run=False)
        assert report.amac_entity_filled == 1
        assert report.csrc_docnum_filled == 1
        assert report.punishment_date_filled == 1
        amac = store.cases.get(Dataset.AMAC, "P1")
        assert amac is not None
        assert amac.punished_entity == "测试私募基金管理有限公司"
        csrc = store.cases.get(Dataset.CSRC, "C1")
        assert csrc is not None
        assert csrc.document_number == "〔2023〕9号"
        summary = store.summaries.get(Dataset.AMAC, "P1")
        assert summary is not None
        assert summary.punishment_date == "2026-05-11"
        # CaseRow 合并展示
        rows = store.cases.search(CaseQuery(datasets=(Dataset.AMAC,)))
        assert rows and rows[0].punished_entities == "测试私募基金管理有限公司"

    def test_repair_does_not_overwrite(self, store: DataStore) -> None:
        _seed(store)
        store.cases.set_punished_entity(Dataset.AMAC, "P1", "已有当事人", only_if_empty=False)
        store.cases.set_document_number(Dataset.CSRC, "C1", "〔2020〕1号", only_if_empty=False)
        report = DataRepairService(store).repair(dry_run=False)
        assert report.amac_entity_filled == 0
        assert report.csrc_docnum_filled == 0
        amac = store.cases.get(Dataset.AMAC, "P1")
        assert amac is not None
        assert amac.punished_entity == "已有当事人"
