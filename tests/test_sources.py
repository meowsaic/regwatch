"""采集层纯函数测试（不发网络请求）。"""

from __future__ import annotations

import unittest
from datetime import date

from regwatch import org_type
from regwatch.sources import amac, csrc


class CsrcUtilsTests(unittest.TestCase):
    def test_extract_id_from_url(self):
        self.assertEqual(
            csrc.extract_id_from_url("https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"),
            "c7615688",
        )
        self.assertEqual(
            csrc.extract_id_from_url("https://www.csrc.gov.cn/xyz/c1234567/abc"),
            "c1234567",
        )

    def test_build_case_id(self):
        url = "https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"
        self.assertEqual(csrc.build_case_id("2026-02-13", url), "20260213_c7615688")
        self.assertEqual(csrc.build_case_id("", url), "c7615688")

    def test_parse_date_from_text(self):
        self.assertEqual(csrc.parse_date_from_text("2026-02-13"), date(2026, 2, 13))
        self.assertEqual(csrc.parse_date_from_text("2026年2月13日"), date(2026, 2, 13))
        self.assertEqual(csrc.parse_date_from_text("发布时间：2025-12-31 08:30"), date(2025, 12, 31))
        self.assertIsNone(csrc.parse_date_from_text(""))
        self.assertIsNone(csrc.parse_date_from_text("not a date"))

    def test_sanitize_filename(self):
        self.assertEqual(csrc.sanitize_filename('a/b:c*d?e"f<g>h|i'), "abcdefghi")

    def test_is_fund_by_title(self):
        self.assertTrue(csrc.is_fund_by_title("关于对某私募基金管理人采取警示函措施的决定"))
        self.assertFalse(csrc.is_fund_by_title("关于对XX证券股份有限公司的行政处罚决定"))
        self.assertFalse(csrc.is_fund_by_title("关于对XX期货有限公司采取监管措施的决定"))
        self.assertIsNone(csrc.is_fund_by_title("关于对XX投资管理有限公司采取警示函措施的决定"))
        self.assertIsNone(csrc.is_fund_by_title(""))

    def test_is_fund_by_content(self):
        self.assertTrue(csrc.is_fund_by_content("当事人XX为私募基金管理人，登记编号..."))
        self.assertTrue(csrc.is_fund_by_content("经查，XX私募基金存在违规..."))
        self.assertFalse(csrc.is_fund_by_content("当事人为证券公司，违反证券法..."))
        self.assertFalse(csrc.is_fund_by_content(""))

    def test_extract_document_number(self):
        self.assertEqual(csrc.extract_document_number("当事人：某某公司。沪证监〔2023〕31号"), "沪证监〔2023〕31号")
        self.assertEqual(csrc.extract_document_number(""), "")

    def test_extract_org_name_from_title(self):
        self.assertEqual(
            csrc.extract_org_name_from_title("关于对某投资管理有限公司采取警示函措施的决定"),
            "某投资管理有限公司",
        )
        self.assertIsNone(csrc.extract_org_name_from_title("无机构名称的标题"))

    def test_extract_punished_entities_prefers_title(self):
        title = "关于对某基金管理有限公司采取警示函措施的决定"
        self.assertEqual(csrc.extract_punished_entities(title, ""), "某基金管理有限公司")

    def test_available_bureaus(self):
        items = csrc.available_bureaus()
        self.assertEqual(len(items), 37)
        self.assertEqual(items[0], {"name_en": "HQ", "name_cn": "证监会"})

    def test_default_start_date(self):
        self.assertEqual(csrc.default_start_date(), date(2022, 1, 1))

    def test_case_type_labels(self):
        self.assertEqual(csrc.CASE_TYPE_CN["penalty"], "行政处罚")
        self.assertEqual(csrc.CASE_TYPE_CN["measure"], "监管措施")


class AmacUtilsTests(unittest.TestCase):
    def test_extract_id_from_url(self):
        self.assertEqual(
            amac.extract_id_from_url("https://www.amac.org.cn/x/P020260109619175763031.pdf"),
            "P020260109619175763031",
        )
        self.assertTrue(amac.extract_id_from_url("https://www.amac.org.cn/a/b.html"))

    def test_sanitize_filename(self):
        self.assertEqual(amac.sanitize_filename('a/b:c*d?e"f<g>h|i'), "abcdefghi")

    def test_categories(self):
        self.assertEqual(set(amac.CATEGORIES), {"Institution", "Personnel"})
        self.assertEqual(amac.CATEGORIES["Institution"]["name"], "受处分机构")

    def test_case_index_alias(self):
        from regwatch.storage import AmacIndex
        self.assertIs(amac.CaseIndex, AmacIndex)

    def test_get_last_quarter_range(self):
        start, end = amac.get_last_quarter_range()
        self.assertLess(start, end)
        self.assertIn(start.month, (1, 4, 7, 10))
        self.assertEqual(start.day, 1)


class OrgTypeTests(unittest.TestCase):
    def test_org_type_values(self):
        self.assertEqual(len(org_type.ORG_TYPE_VALUES), 6)
        self.assertIn("私募证券投资基金管理人", org_type.ORG_TYPE_VALUES)

    def test_extract_org_type_from_text(self):
        text = "当事人为私募股权、创业投资基金管理人，登记编号P1234567，存在违规行为。" + "补充" * 60
        self.assertEqual(
            org_type.extract_org_type_from_text("某公司", text),
            "私募股权、创业投资基金管理人",
        )
        self.assertIsNone(org_type.extract_org_type_from_text("某公司", "正文不含登记类型"))
        self.assertIsNone(org_type.extract_org_type_from_text("某公司", ""))

    def test_extract_org_name_from_title(self):
        self.assertEqual(
            org_type.extract_org_name_from_title("关于对某投资管理有限公司的纪律处分决定书"),
            "某投资管理有限公司",
        )
        self.assertIsNone(org_type.extract_org_name_from_title("没有机构名"))

    def test_extract_full_org_name_from_text(self):
        text = "被申请人：某某创业投资管理有限公司（以下简称某创投），存在违规。" + "内容" * 20
        self.assertEqual(
            org_type.extract_full_org_name_from_text("某创投", text),
            "某某创业投资管理有限公司",
        )

    def test_extract_punished_entity_skips_personnel(self):
        self.assertEqual(
            org_type.extract_punished_entity("关于对某投资管理有限公司的纪律处分决定书", "", "Personnel"),
            "",
        )

    def test_resolve_org_type_uses_cache_first(self):
        class FakeCache:
            def __init__(self):
                self.hits = 0

            def get(self, name):
                self.hits += 1
                return "私募证券投资基金管理人"

            def set(self, name, value):  # pragma: no cover - 缓存命中时不会调用
                raise AssertionError("缓存命中时不应写入")

        cache = FakeCache()
        result = org_type.resolve_org_type(
            "关于对某投资管理有限公司的纪律处分决定书", "", "Institution", cache, "某投资管理有限公司",
        )
        self.assertEqual(result, "私募证券投资基金管理人")
        self.assertEqual(cache.hits, 1)

    def test_resolve_org_type_ignores_non_institution(self):
        self.assertEqual(
            org_type.resolve_org_type("标题", "正文", "Personnel", None, "某公司"),
            "",
        )

    def test_backfill_result_to_dict(self):
        result = org_type.BackfillResult(total=10, org_type_filled=3)
        data = result.to_dict()
        self.assertEqual(data["total"], 10)
        self.assertEqual(data["manual_count"], 0)
        self.assertFalse(data["dry_run"])


if __name__ == "__main__":
    unittest.main()
