"""证监局来源配置与工具函数的纯本地测试（不发网络请求）。

由原 ``CSRC/test_smoke.py`` 迁移并修正过时断言：
原测试引用了 ``is_pf_by_title`` / ``skipped_not_pf`` / ``CaseData.entity_type`` 等
已不存在的符号，本文件统一改为当前实现的实际名称。
"""

from __future__ import annotations

import unittest

from regwatch.sources import csrc_bureaus
from regwatch.sources.csrc import CaseData
from regwatch.sources.csrc_bureaus import (
    BUREAUS,
    discover_penalty_url,
    get_bureau_by_code,
    get_bureau_by_name,
)

EXPECTED_BUREAU_COUNT = 37


class BureausConfigTests(unittest.TestCase):
    def test_bureau_count(self):
        self.assertEqual(len(BUREAUS), EXPECTED_BUREAU_COUNT)

    def test_head_office_fields(self):
        hq = BUREAUS[0]
        self.assertEqual(hq.name_cn, "证监会")
        self.assertEqual(hq.name_en, "HQ")
        self.assertEqual(hq.path_code, "c106259")
        self.assertEqual(
            hq.measure_url,
            "https://www.csrc.gov.cn/csrc/c106259/common_list_gd.shtml",
        )
        self.assertEqual(hq.penalty_channelid, "28de6b87eda140cb93de4dd10d11867d")
        self.assertIn("common_list.shtml", hq.penalty_url)
        self.assertEqual(hq.site_path, "csrc")

    def test_unique_keys(self):
        codes = [item.path_code for item in BUREAUS]
        names_en = [item.name_en for item in BUREAUS]
        names_cn = [item.name_cn for item in BUREAUS]
        self.assertEqual(len(set(codes)), len(codes), "path_code 应全局唯一")
        self.assertEqual(len(set(names_en)), len(names_en), "name_en 应全局唯一")
        self.assertEqual(len(set(names_cn)), len(names_cn), "name_cn 应全局唯一")

    def test_all_site_paths_present(self):
        empty = [item.name_en for item in BUREAUS if not item.site_path]
        self.assertEqual(empty, [], f"以下来源缺少 site_path: {empty}")

    def test_all_measure_urls_follow_pattern(self):
        bad = []
        for item in BUREAUS:
            expected = f"https://www.csrc.gov.cn/csrc/{item.path_code}/common_list_gd.shtml"
            if item.measure_url != expected:
                bad.append((item.name_en, item.measure_url))
        self.assertEqual(bad, [], f"监管措施 URL 不符合规范: {bad}")

    def test_lookup_by_name(self):
        shanghai = get_bureau_by_name("Shanghai")
        self.assertIsNotNone(shanghai)
        assert shanghai is not None
        self.assertEqual(shanghai.name_cn, "上海证监局")
        self.assertIn("shanghai", shanghai.penalty_url)
        self.assertEqual(get_bureau_by_name("NotExist"), None)

    def test_lookup_by_code(self):
        by_code = get_bureau_by_code("c100053")
        self.assertIsNotNone(by_code)
        assert by_code is not None
        self.assertEqual(by_code.name_en, "Shanghai")
        # 不带 c 前缀也应能匹配
        self.assertEqual(get_bureau_by_code("100053").name_en, "Shanghai")
        self.assertEqual(get_bureau_by_code("c999999"), None)

    def test_discover_penalty_url_short_circuits(self):
        """已配置 penalty_url 的来源应直接返回，不发起网络请求。"""
        hq = BUREAUS[0]
        self.assertEqual(discover_penalty_url(hq), hq.penalty_url)

        without_penalty = [item for item in BUREAUS if not item.penalty_url]
        for item in without_penalty:
            # site_path 非空但未配置 penalty_url 时会联网，这里只验证不抛异常的结构前提
            self.assertTrue(item.site_path)

    def test_module_exports(self):
        for name in (
            "Bureau",
            "BUREAUS",
            "discover_penalty_url",
            "get_bureau_by_name",
            "get_bureau_by_code",
        ):
            self.assertTrue(hasattr(csrc_bureaus, name), f"缺少导出符号 {name}")


class CsrcCaseDataTests(unittest.TestCase):
    def test_field_set_is_stable(self):
        expected = {
            "case_id",
            "source_url",
            "case_type",
            "bureau",
            "title",
            "date",
            "raw_text",
            "fetch_time",
            "error",
            "is_fund_related",
            "fund_evidence",
            "document_number",
            "punished_entities",
            "pdf_url",
            "doc_url",
        }
        self.assertEqual(set(CaseData.__dataclass_fields__), expected)

    def test_defaults(self):
        case = CaseData(
            case_id="x",
            source_url="u",
            case_type="measure",
            bureau="HQ",
            title="t",
            date="2025-01-01",
        )
        self.assertEqual(case.raw_text, "")
        self.assertFalse(case.is_fund_related)


if __name__ == "__main__":
    unittest.main()
