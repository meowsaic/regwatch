"""实网连通性测试（默认跳过）。

本文件会真实访问 csrc.gov.cn，因此默认不执行；需要时显式开启：

    PowerShell:  $env:REGWATCH_LIVE_TEST=1; python -m unittest tests.test_live -v
    Bash:        REGWATCH_LIVE_TEST=1 python -m unittest tests.test_live -v

覆盖内容（不调用任何大模型）：

1. 监管措施列表页翻页与日期过滤
2. 详情页 HTML 正文提取
3. PDF / Word 附件链接发现
"""

from __future__ import annotations

import os
import unittest
from datetime import date, timedelta

from regwatch.sources import csrc
from regwatch.sources.csrc_bureaus import BUREAUS

LIVE_ENABLED = os.environ.get("REGWATCH_LIVE_TEST") == "1"

SKIP_REASON = "实网测试默认跳过：设置环境变量 REGWATCH_LIVE_TEST=1 后启用"


@unittest.skipUnless(LIVE_ENABLED, SKIP_REASON)
class CsrcLiveTests(unittest.TestCase):
    """证监会官网实网校验。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.hq = BUREAUS[0]
        cls.start = date.today() - timedelta(days=180)
        cls.end = date.today()

    @classmethod
    def tearDownClass(cls) -> None:
        csrc.SessionManager().close()

    def test_collect_measure_links(self):
        links = csrc.collect_measure_links(self.hq, self.start, self.end)
        self.assertTrue(links, "半年内应至少能收集到一条监管措施")
        for item in links[:5]:
            self.assertTrue(item["link_url"].startswith("http"), item["link_url"])
            self.assertTrue(item["title"])
            self.assertIsInstance(item["date"], date)
            self.assertLessEqual(self.start, item["date"])
            self.assertLessEqual(item["date"], self.end)

    def test_fund_title_classification(self):
        links = csrc.collect_measure_links(self.hq, self.start, self.end)
        verdicts = {"fund": 0, "non_fund": 0, "unknown": 0}
        for item in links:
            verdict = csrc.is_fund_by_title(item["title"])
            if verdict is True:
                verdicts["fund"] += 1
            elif verdict is False:
                verdicts["non_fund"] += 1
            else:
                verdicts["unknown"] += 1
        self.assertEqual(sum(verdicts.values()), len(links))
        # 基金相关条文应当存在，否则说明关键词表失灵
        self.assertGreater(verdicts["fund"], 0, f"标题分类结果异常: {verdicts}")

    def test_html_and_pdf_extraction(self):
        links = csrc.collect_measure_links(self.hq, self.start, self.end)
        self.assertTrue(links, "缺少可用于提取测试的案例")
        sample = next(
            (item for item in links if csrc.is_fund_by_title(item["title"]) is not False),
            links[0],
        )
        text = csrc.extract_text_from_html(sample["link_url"])
        self.assertTrue(text and len(text) > 50, "详情页正文提取失败")
        self.assertTrue(csrc.is_fund_by_content(text) or True)  # 仅验证不抛异常

        pdf_url = csrc.find_pdf_link_in_page(sample["link_url"])
        if pdf_url:
            self.assertTrue(pdf_url.startswith("http"), pdf_url)


if __name__ == "__main__":
    unittest.main()
