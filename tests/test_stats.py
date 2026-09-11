"""统计与报告层的纯本地测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from regwatch.analyze import (
    analyze,
    compute_basic_stats,
    compute_bureau_stats,
    compute_dataset_split,
    compute_entity_comparison,
    compute_legal_basis_stats,
    compute_punishment_stats,
    compute_time_trend,
    compute_violation_stats,
    normalize_violation_types,
    pick_representative_cases,
)
from regwatch.config import Config
from regwatch.report import (
    build_report,
    default_compliance_advice,
    get_last_quarter_label,
    markdown_to_html,
    render_html,
    render_markdown,
    save_report_files,
)
from regwatch.storage import CaseRow


def row(**kwargs) -> CaseRow:
    defaults = dict(dataset="amac", case_id="x", date="2025-01-01")
    defaults.update(kwargs)
    return CaseRow(**defaults)


ROWS = [
    row(case_id="a", date="2025-01-05", entity="甲公司", entity_type="机构",
        violation_type="违规募集、内控缺失", punishment="公开谴责",
        legal_basis="《私募投资基金监督管理暂行办法》第十二条",
        violation_summary="向不合格投资者募集基金份额，情节严重。"),
    row(case_id="b", date="2025-01-20", entity="乙公司", entity_type="机构",
        violation_type="信息披露违规", punishment="暂停受理备案",
        legal_basis="《私募投资基金监督管理暂行办法》第二十四条",
        violation_summary="未按合同约定向投资者披露基金净值信息。"),
    row(case_id="c", date="2025-02-10", entity="张三", entity_type="个人",
        violation_type="挪用基金财产、违规募集", punishment="加入黑名单",
        legal_basis="《私募投资基金监督管理暂行办法》第二十三条",
        violation_summary="挪用基金财产用于个人支出，并承诺保本保收益。"),
    row(case_id="d", date="2025-02-25", entity="丙证券", entity_type="机构",
        violation_type="信息披露违规", punishment="出具警示函",
        legal_basis="《证券法》第一百九十七条",
        violation_summary="未按规定披露重大事项。", bureau="Beijing",
        case_type="measure"),
]


class NormalizeTests(unittest.TestCase):
    def test_split_and_normalize(self):
        self.assertEqual(
            normalize_violation_types("违规募集、内控缺失"),
            ["违规募集", "内控缺失"],
        )

    def test_fragment_containing_known_type(self):
        # 片段描述更细，但包含体系内类型时应归一
        self.assertEqual(
            normalize_violation_types("承诺保本保收益"),
            ["承诺保本保收益"],
        )

    def test_unknown_type_preserved(self):
        self.assertEqual(normalize_violation_types("异常新类型"), ["异常新类型"])

    def test_empty_becomes_uncategorized(self):
        self.assertEqual(normalize_violation_types(""), ["未分类"])

    def test_deduplicated(self):
        self.assertEqual(normalize_violation_types("内控缺失、内控缺失"), ["内控缺失"])


class StatsTests(unittest.TestCase):
    def test_basic_stats(self):
        stats = compute_basic_stats(ROWS)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["institution_count"], 3)
        self.assertEqual(stats["personnel_count"], 1)
        self.assertEqual(stats["date_range"], "2025-01-05 ~ 2025-02-25")

    def test_violation_stats_counts_each_mention(self):
        stats = compute_violation_stats(ROWS)
        counter = stats["counter"]
        self.assertEqual(counter["违规募集"], 2)
        self.assertEqual(counter["信息披露违规"], 2)
        self.assertEqual(counter["挪用基金财产"], 1)
        # 案例映射可用于代表案例挑选
        self.assertEqual(len(stats["case_map"]["内控缺失"]), 1)

    def test_punishment_stats_categories(self):
        stats = compute_punishment_stats(ROWS)
        categories = dict(stats["category_ranked"])
        self.assertEqual(categories.get("公开谴责"), 1)
        self.assertEqual(categories.get("加入黑名单"), 1)
        self.assertEqual(categories.get("出具警示函"), 1)
        self.assertEqual(dict(stats["counter"])["公开谴责"], 1)

    def test_punishment_unclassified_goes_to_other(self):
        stats = compute_punishment_stats([row(case_id="z", punishment="不常见的措施")])
        self.assertEqual(dict(stats["category_ranked"]).get("其他"), 1)

    def test_legal_basis_extract_law_names(self):
        stats = compute_legal_basis_stats(ROWS)
        ranked = dict(stats["ranked"])
        self.assertEqual(ranked.get("私募投资基金监督管理暂行办法"), 3)
        self.assertEqual(ranked.get("证券法"), 1)

    def test_legal_basis_skips_bare_articles(self):
        stats = compute_legal_basis_stats([row(case_id="z", legal_basis="第十二条、第十三条")])
        self.assertEqual(stats["ranked"], [])

    def test_entity_comparison(self):
        comparison = compute_entity_comparison(ROWS)
        inst = dict(comparison["inst_violations"])
        pers = dict(comparison["pers_violations"])
        self.assertEqual(inst.get("信息披露违规"), 2)
        self.assertEqual(pers.get("挪用基金财产"), 1)
        self.assertIn("加入黑名单", [name for name, _ in comparison["pers_punishments"].most_common()])

    def test_time_trend_monthly(self):
        trend = compute_time_trend(ROWS)
        by_period = {item["period"]: item for item in trend}
        self.assertEqual(by_period["2025-01"]["count"], 2)
        self.assertEqual(by_period["2025-02"]["count"], 2)
        self.assertEqual(by_period["2025-02"]["personnel"], 1)

    def test_time_trend_yearly(self):
        trend = compute_time_trend(ROWS, granularity="year")
        self.assertEqual(trend[0]["period"], "2025")
        self.assertEqual(trend[0]["count"], 4)

    def test_bureau_and_dataset_split(self):
        self.assertEqual(compute_bureau_stats(ROWS), [("Beijing", 1)])
        split = {item["dataset"]: item["count"] for item in compute_dataset_split(ROWS)}
        self.assertEqual(split["amac"], 4)

    def test_pick_representative_prefers_longer_summary(self):
        violation = compute_violation_stats(ROWS)
        representative = pick_representative_cases(violation["case_map"])
        chosen = representative["违规募集"][0]
        self.assertEqual(chosen.case_id, "c")  # 摘要更长


class AnalyzeTests(unittest.TestCase):
    def test_analyze_payload_is_json_ready(self):
        result = analyze(ROWS)
        payload = result.to_json_payload()
        self.assertEqual(payload["basic"]["total"], 4)
        distribution = {item["type"]: item["count"] for item in payload["violation_distribution"]}
        self.assertEqual(distribution["信息披露违规"], 2)
        self.assertEqual(distribution["违规募集"], 2)
        # 排名按数量降序
        counts = [item["count"] for item in payload["violation_distribution"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertTrue(payload["time_trend"])
        self.assertEqual(payload["bureau_top"], [("Beijing", 1)])


class ReportTests(unittest.TestCase):
    def test_markdown_has_all_sections(self):
        result = analyze(ROWS)
        markdown = render_markdown(result, "测试报告", "2025-01-01 ~ 2025-02-28", "中基协（AMAC）")
        for heading in ("## 一、概况", "## 二、违规类型统计", "## 三、处罚措施分析",
                        "## 四、机构 vs 个人对比", "## 五、法规依据分析",
                        "## 六、典型案例", "## 七、合规建议", "## 附件：案例列表"):
            self.assertIn(heading, markdown)
        self.assertIn("《私募投资基金监督管理暂行办法》", markdown)
        # LLM 建议优先于内置建议
        custom = render_markdown(result, "测试报告", "周期", "中基协（AMAC）", advice="这是模型撰写的建议。")
        self.assertIn("这是模型撰写的建议。", custom)
        self.assertNotIn("### 通用合规建议", custom)

    def test_html_rendering(self):
        result = analyze(ROWS)
        markdown = render_markdown(result, "测试报告", "周期", "中基协（AMAC）")
        html = render_html(markdown, "测试报告", "周期", "中基协（AMAC）")
        self.assertIn("<!doctype html>", html)
        self.assertIn("测试报告", html)
        self.assertIn("#354e92", html)  # 主题色
        self.assertIn("<table>", html)

    def test_markdown_to_html_table_and_list(self):
        md = "# 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- 项目一\n- 项目二\n\n> 引用\n"
        html = markdown_to_html(md, title="t")
        self.assertIn("<h1>", html)
        self.assertIn("<th>a</th>", html)
        self.assertIn("<li>项目一</li>", html)
        self.assertIn("<blockquote>", html)
        # 分隔行不应出现在表格体里
        self.assertNotIn("<td>---</td>", html)

    def test_last_quarter_label(self):
        from datetime import datetime

        self.assertEqual(
            get_last_quarter_label(datetime(2026, 5, 11)),
            "2026年Q1（2026-01-01 ~ 2026-03-31）",
        )
        self.assertEqual(
            get_last_quarter_label(datetime(2026, 1, 3)),
            "2025年Q4（2025-10-01 ~ 2025-12-31）",
        )


class BuildReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.cfg = Config(self.base / "config.json")
        self.cfg.data["data_roots"] = {
            "amac_cases": str(self.base / "amac" / "cases"),
            "amac_summaries": str(self.base / "amac" / "summaries"),
            "amac_reports": str(self.base / "amac" / "reports"),
            "csrc_cases": str(self.base / "csrc" / "cases"),
            "csrc_summaries": str(self.base / "csrc" / "summaries"),
            "csrc_reports": str(self.base / "csrc" / "reports"),
        }
        cases = self.base / "amac" / "cases" / "institution"
        cases.mkdir(parents=True)
        summaries = self.base / "amac" / "summaries"
        summaries.mkdir(parents=True)
        for index, case_id in enumerate(("20250101_1001", "20250101_1002"), 1):
            (cases / f"{case_id}.json").write_text(
                '{"case_id": "%s", "raw_text": "正文", "ocr_success": true}' % case_id,
                encoding="utf-8",
            )
            (summaries / f"{case_id}_summary.json").write_text(
                '{"case_id": "%s", "category": "scfjg", "date": "2025-01-0%d", '
                '"punished_entity": "公司%d", "entity_type": "机构", '
                '"violation_type": "内控缺失", "punishment": "公开谴责", '
                '"violation_summary": "内控不健全，合规风控人员兼任冲突职务。", '
                '"extract_success": true}' % (case_id, index, index),
                encoding="utf-8",
            )

    def test_build_report_creates_files(self):
        from regwatch.storage import SummaryIndex

        SummaryIndex(self.base / "amac" / "summaries").mark_done("20250101_1001", "20250101_1001_summary.json")
        SummaryIndex(self.base / "amac" / "summaries").mark_done("20250101_1002", "20250101_1002_summary.json")

        output = build_report("amac", config=self.cfg, start_date="2025-01-01", end_date="2025-12-31")
        self.assertEqual(output.row_count, 2)
        self.assertIn("## 一、概况", output.markdown)
        self.assertIn("2025-01-01 ~ 2025-12-31", output.period_label)

        paths = save_report_files(output, self.base / "out", stem="demo")
        for key in ("md", "html", "json"):
            self.assertIn(key, paths)
            self.assertTrue(Path(paths[key]).exists())

    def test_empty_range_yields_report_without_crash(self):
        output = build_report("amac", config=self.cfg, start_date="1999-01-01", end_date="1999-12-31")
        self.assertEqual(output.row_count, 0)
        self.assertIn("本期暂无可展示的代表性案例", output.markdown)
        self.assertIsNotNone(default_compliance_advice(analyze([])))


if __name__ == "__main__":
    unittest.main()
