"""数据访问层的纯本地测试（使用临时目录构造迷你数据集，不读取真实数据）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from regwatch.config import Config
from regwatch.storage import (
    DATASET_AMAC,
    DATASET_CSRC,
    AmacIndex,
    CaseRow,
    CsrcIndex,
    OrgTypeCache,
    SummaryIndex,
    build_catalog,
    dataset_overview,
    filter_rows,
    invalidate_catalog,
    read_case,
    read_json,
    split_multi_value,
    write_json,
)


class DataRootConfig(Config):
    """把所有数据根目录指向临时目录的配置。"""

    def __init__(self, base: Path) -> None:
        super().__init__(path=base / "config.json")
        self.data["data_roots"] = {
            "amac_cases": str(base / "amac" / "cases"),
            "amac_summaries": str(base / "amac" / "summaries"),
            "amac_reports": str(base / "amac" / "reports"),
            "csrc_cases": str(base / "csrc" / "cases"),
            "csrc_summaries": str(base / "csrc" / "summaries"),
        }


def make_amac_fixture(base: Path) -> None:
    cases = base / "amac" / "cases"
    summaries = base / "amac" / "summaries"
    (cases / "institution").mkdir(parents=True)
    (cases / "personnel").mkdir(parents=True)
    summaries.mkdir(parents=True)

    write_json(
        cases / "institution" / "20250101_1001.json",
        {
            "case_id": "20250101_1001",
            "category": "scfjg",
            "title": "某投资管理公司纪律处分",
            "date": "2025-01-01",
            "raw_text": "正文" * 40,
            "ocr_success": True,
            "org_type": "私募证券投资基金管理人",
            "punished_entity": "某投资管理有限公司",
        },
    )
    write_json(
        summaries / "20250101_1001_summary.json",
        {
            "case_id": "20250101_1001",
            "category": "scfjg",
            "title": "某投资管理公司纪律处分",
            "date": "2025-01-01",
            "punished_entity": "某投资管理有限公司",
            "entity_type": "机构",
            "org_type": "私募证券投资基金管理人",
            "violation_type": "违规募集、内控缺失",
            "punishment": "公开谴责",
            "violation_summary": "摘要",
            "legal_basis": "《私募办法》",
            "extract_success": True,
        },
    )

    index = AmacIndex(cases)
    index.update_category(
        "Institution",
        [
            {
                "link_url": "https://amac.example/1",
                "title": "某投资管理公司纪律处分",
                "date": "2025-01-01",
            },
        ],
        last_page=0,
    )
    index.mark_done("Institution", "https://amac.example/1")

    summary_index = SummaryIndex(summaries)
    summary_index.mark_done("20250101_1001", "20250101_1001_summary.json")


def make_csrc_fixture(base: Path) -> None:
    cases = base / "csrc" / "cases"
    summaries = base / "csrc" / "summaries"
    (cases / "Beijing" / "measure").mkdir(parents=True)
    (summaries / "Beijing" / "measure").mkdir(parents=True)

    write_json(
        cases / "Beijing" / "measure" / "20250301_c1234567.json",
        {
            "case_id": "20250301_c1234567",
            "bureau": "Beijing",
            "case_type": "measure",
            "title": "关于对某基金管理有限公司采取警示函措施的决定",
            "date": "2025-03-01",
            "raw_text": "正文" * 50,
            "punished_entities": "某基金管理有限公司",
            "document_number": "京证监〔2025〕1号",
            "is_fund_related": True,
            "fund_evidence": "标题含'基金'",
        },
    )
    write_json(
        summaries / "Beijing" / "measure" / "20250301_c1234567_summary.json",
        {
            "case_id": "20250301_c1234567",
            "bureau": "Beijing",
            "case_type": "measure",
            "title": "关于对某基金管理有限公司采取警示函措施的决定",
            "date": "2025-03-01",
            "punished_entities": "某基金管理有限公司",
            "entity_type": "机构",
            "violation_type": "内控缺失",
            "punishment": "出具警示函",
            "violation_summary": "内控不健全",
            "extract_success": True,
        },
    )

    index = CsrcIndex(cases)
    index.update_source(
        "Beijing/measure",
        [
            {
                "link_url": "https://csrc.example/x/c1234567/content.shtml",
                "title": "关于对某基金管理有限公司采取警示函措施的决定",
                "date": "2025-03-01",
            },
            {
                "link_url": "https://csrc.example/x/c7654321/content.shtml",
                "title": "关于对某证券公司的行政处罚决定",
                "date": "2025-02-01",
            },
        ],
        last_page=0,
    )
    index.mark_done("Beijing/measure", "https://csrc.example/x/c1234567/content.shtml")
    index.mark_skipped(
        "Beijing/measure", "https://csrc.example/x/c7654321/content.shtml", "title_non_fund"
    )

    summary_index = SummaryIndex(summaries)
    summary_index.mark_done("20250301_c1234567", "Beijing/measure/20250301_c1234567_summary.json")
    summary_index.mark_skipped("20250201_c7654321", "当事人为证券公司，非基金业务")


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.cfg = DataRootConfig(self.base)
        make_amac_fixture(self.base)
        make_csrc_fixture(self.base)
        invalidate_catalog()

    # ── 基础 IO ──

    def test_write_and_read_json(self):
        path = self.base / "a" / "b.json"
        write_json(path, {"中文": "值"})
        self.assertEqual(read_json(path), {"中文": "值"})
        self.assertIsNone(read_json(self.base / "missing.json"))
        path.write_text("{ 坏 JSON", encoding="utf-8")
        self.assertIsNone(read_json(path))

    def test_split_multi_value(self):
        self.assertEqual(split_multi_value("违规募集、内控缺失"), ["违规募集", "内控缺失"])
        self.assertEqual(split_multi_value("A；B;C，D"), ["A", "B", "C", "D"])
        self.assertEqual(split_multi_value(""), [])
        # 去重且保持顺序
        self.assertEqual(split_multi_value("A、A、B"), ["A", "B"])

    # ── 索引 ──

    def test_amac_index_roundtrip(self):
        index = AmacIndex(self.base / "amac" / "cases")
        stats = index.get_stats("Institution")
        self.assertEqual((stats["total"], stats["done"]), (1, 1))
        self.assertEqual(index.get_pending_links("Personnel"), [])

        index.update_category(
            "Institution",
            [
                {"link_url": "https://amac.example/1", "title": "改后标题", "date": "2025-01-01"},
                {"link_url": "https://amac.example/2", "title": "新案例", "date": "2025-02-01"},
            ],
            last_page=3,
        )
        stats = index.get_stats("Institution")
        self.assertEqual((stats["total"], stats["done"], stats["pending"]), (2, 1, 1))
        self.assertEqual(index.get_last_crawled_page("Institution"), 3)
        self.assertEqual(index.get_category_links("Institution")[0]["title"], "改后标题")

    def test_csrc_index_statuses(self):
        index = CsrcIndex(self.base / "csrc" / "cases")
        stats = index.get_stats("Beijing/measure")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["done"], 1)
        self.assertEqual(stats["skipped_not_fund"], 1)

        index.mark_failed(
            "Beijing/measure", "https://csrc.example/x/c1234567/content.shtml", "网络错误"
        )
        self.assertEqual(index.get_stats("Beijing/measure")["failed"], 1)
        lookup = index.link_lookup_by_content_id()
        self.assertIn("c7654321", lookup)
        self.assertEqual(lookup["c7654321"]["bureau"], "Beijing")

    def test_summary_index_states(self):
        index = SummaryIndex(self.base / "amac" / "summaries")
        self.assertTrue(index.is_done("20250101_1001"))
        self.assertFalse(index.is_done("20250101_9999"))
        self.assertEqual(
            index.get_pending_cases(["20250101_1001", "20250101_9999"]), ["20250101_9999"]
        )

        index.mark_failed("20250101_9999", "模型超时")
        self.assertEqual(index.get_failed_cases(), ["20250101_9999"])
        stats = index.get_stats(total_count=3)
        self.assertEqual((stats["done"], stats["failed"], stats["pending"]), (1, 1, 1))

        index.mark_skipped("20250102_8888", "非基金相关")
        self.assertTrue(index.is_done("20250102_8888"))
        self.assertEqual(index.get_skipped_cases(), ["20250102_8888"])

        index.forget("20250102_8888")
        self.assertFalse(index.is_done("20250102_8888"))

    def test_org_type_cache(self):
        cache = OrgTypeCache(self.base / "amac" / "cases")
        self.assertIsNone(cache.get("某公司"))
        cache.set("某公司", "私募股权、创业投资基金管理人")
        self.assertEqual(cache.get("某公司"), "私募股权、创业投资基金管理人")
        self.assertEqual(len(OrgTypeCache(self.base / "amac" / "cases")), 1)
        cache.set("", "x")
        self.assertEqual(len(cache), 1)

    # ── 清单 ──

    def test_build_catalog_amac(self):
        rows = build_catalog(DATASET_AMAC, self.cfg)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.case_id, "20250101_1001")
        self.assertEqual(row.entity_type, "机构")
        self.assertEqual(row.violation_types, ["违规募集", "内控缺失"])
        self.assertTrue(row.case_file.endswith("20250101_1001.json"))
        self.assertEqual(row.category_label, "机构")

    def test_build_catalog_csrc_includes_skipped(self):
        rows = build_catalog(DATASET_CSRC, self.cfg)
        self.assertEqual(len(rows), 2)
        by_id = {row.case_id: row for row in rows}
        done = by_id["20250301_c1234567"]
        self.assertEqual(done.status, "done")
        self.assertEqual(
            done.case_type_label, "行政处罚" if done.case_type == "penalty" else "监管措施"
        )
        skipped = by_id["20250201_c7654321"]
        self.assertEqual(skipped.status, "skipped")
        # 标题与机构信息由抓取索引补全
        self.assertIn("证券公司", skipped.title)
        self.assertEqual(skipped.bureau, "Beijing")
        self.assertIn("非基金", skipped.note)
        # 排序为日期倒序
        self.assertEqual([row.date for row in rows], ["2025-03-01", "2025-02-01"])

    def test_catalog_cache_and_invalidation(self):
        first = build_catalog(DATASET_AMAC, self.cfg)
        second = build_catalog(DATASET_AMAC, self.cfg)
        self.assertIs(first, second)

        write_json(
            self.base / "amac" / "summaries" / "20250501_1002_summary.json",
            {
                "case_id": "20250501_1002",
                "category": "scfry",
                "date": "2025-05-01",
                "punished_entity": "张三",
                "entity_type": "个人",
                "violation_type": "内控缺失",
                "extract_success": True,
            },
        )
        SummaryIndex(self.base / "amac" / "summaries").mark_done(
            "20250501_1002", "20250501_1002_summary.json"
        )
        rows = build_catalog(DATASET_AMAC, self.cfg)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].case_id, "20250501_1002")

        invalidate_catalog(DATASET_AMAC)
        self.assertEqual(len(build_catalog(DATASET_AMAC, self.cfg)), 2)

    # ── 筛选与读取 ──

    def test_filter_rows(self):
        rows = build_catalog(DATASET_CSRC, self.cfg)
        self.assertEqual(len(filter_rows(rows, statuses=["done"])), 1)
        self.assertEqual(len(filter_rows(rows, datasets=[DATASET_AMAC])), 0)
        self.assertEqual(len(filter_rows(rows, violation_types=["内控缺失"])), 1)
        self.assertEqual(len(filter_rows(rows, violation_types=["挪用基金财产"])), 0)
        # 只有已生成摘要的案例才有主体类型；被跳过的案例无摘要字段
        self.assertEqual(len(filter_rows(rows, entity_types=["机构"])), 1)
        self.assertEqual(len(filter_rows(rows, case_types=["measure"])), 2)
        self.assertEqual(len(filter_rows(rows, date_from="2025-03-01")), 1)
        self.assertEqual(len(filter_rows(rows, date_to="2025-02-28")), 1)
        self.assertEqual(len(filter_rows(rows, keyword="警示函")), 1)
        self.assertEqual(len(filter_rows(rows, bureaus=["Beijing"])), 2)
        self.assertEqual(len(filter_rows(rows, limit=1)), 1)

    def test_read_case(self):
        case = read_case(DATASET_CSRC, "20250301_c1234567", "Beijing", "measure", config=self.cfg)
        self.assertIsNotNone(case)
        assert case is not None
        self.assertTrue(case["raw_text"].startswith("正文"))

        case = read_case(DATASET_AMAC, "20250101_1001", category="scfjg", config=self.cfg)
        self.assertIsNotNone(case)
        assert case is not None
        self.assertEqual(case["punished_entity"], "某投资管理有限公司")

        self.assertIsNone(read_case(DATASET_AMAC, "不存在", config=self.cfg))

    # ── 概览 ──

    def test_dataset_overview(self):
        amac = dataset_overview(DATASET_AMAC, self.cfg)
        self.assertEqual(amac.case_files, 1)
        self.assertEqual(amac.summary_files, 1)
        self.assertEqual(amac.done, 1)
        self.assertEqual(amac.institutions, 1)
        self.assertEqual((amac.date_min, amac.date_max), ("2025-01-01", "2025-01-01"))

        csrc = dataset_overview(DATASET_CSRC, self.cfg)
        self.assertEqual(csrc.done, 1)
        self.assertEqual(csrc.skipped, 1)
        self.assertEqual(csrc.case_files, 1)

    def test_unknown_dataset_raises(self):
        with self.assertRaises(ValueError):
            build_catalog("unknown", self.cfg)


class CaseRowTests(unittest.TestCase):
    def test_violation_types_and_labels(self):
        row = CaseRow(dataset=DATASET_AMAC, case_id="x", violation_type="A、B", category="scfry")
        self.assertEqual(row.violation_types, ["A", "B"])
        self.assertEqual(row.category_label, "人员")
        self.assertEqual(row.year, "")
        self.assertEqual(row.entity_display, "x")

    def test_to_dict_contains_derived_fields(self):
        row = CaseRow(dataset=DATASET_CSRC, case_id="x", date="2025-06-01", case_type="penalty")
        data = row.to_dict()
        self.assertEqual(data["month"], "2025-06")
        self.assertEqual(data["case_type_label"], "行政处罚")
        self.assertEqual(data["year"], "2025")


if __name__ == "__main__":
    unittest.main()
