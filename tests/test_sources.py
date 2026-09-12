"""采集层测试：共享工具、HTML 解析、CSRC 纯函数与来源配置。"""

from __future__ import annotations

from regwatch.sources.bureaus import BUREAUS, get_bureau_by_name
from regwatch.sources.common import (
    extract_amac_case_id,
    extract_csrc_case_id,
    parse_date_from_text,
    sanitize_filename,
)
from regwatch.sources.csrc.discovery import _parse_list_items_from_soup
from regwatch.sources.csrc.models import (
    build_case_id,
    extract_document_number,
    extract_punished_entities,
    is_fund_by_content,
    is_fund_by_title,
    is_non_case_title,
)
from regwatch.sources.htmlparse import extract_main_text, find_attachment_link, parse_html


class TestCommon:
    def test_sanitize_filename(self) -> None:
        assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"

    def test_parse_date_from_text_supports_two_formats(self) -> None:
        assert parse_date_from_text("发布日期 2026-01-05") is not None
        assert parse_date_from_text("二〇二六年 2026年1月5日") is not None

    def test_parse_date_rejects_impossible_date(self) -> None:
        assert parse_date_from_text("2026-02-31") is None
        assert parse_date_from_text("") is None

    def test_extract_amac_case_id(self) -> None:
        assert extract_amac_case_id("https://x/t20260105_1.html") == "20260105_1"
        assert extract_amac_case_id("https://x/P12345678901234567890.html").startswith("P")

    def test_extract_csrc_case_id(self) -> None:
        url = "https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"
        assert extract_csrc_case_id(url) == "c7615688"


class TestHtmlParse:
    def test_extract_main_text(self) -> None:
        html = (
            "<html><body><div class='detail-news'>"
            "<p>第一段正文内容，篇幅足够长以便通过最小长度检查。</p>"
            "<p>第二段正文内容，同样足够长以便通过最小长度检查。</p>"
            "</div></body></html>"
        )
        text = extract_main_text(parse_html(html), min_length=10)
        assert text is not None and "第一段正文内容" in text

    def test_inline_tags_do_not_break_lines(self) -> None:
        html = (
            "<div class='detail-news'><p><span>甲</span><span>乙</span>"
            "<span>丙</span><span>丁</span><span>戊</span><span>己</span>"
            "<span>庚</span><span>辛</span><span>壬</span><span>癸</span></p></div>"
        )
        text = extract_main_text(parse_html(html), min_length=5)
        assert text == "甲乙丙丁戊己庚辛壬癸"

    def test_short_text_returns_none(self) -> None:
        assert extract_main_text(parse_html("<div class='detail-news'>短</div>")) is None

    def test_find_attachment_link_prefers_attachment_area(self) -> None:
        html = (
            "<html><body>"
            "<div class='attachment-down'><a href='/files/a.pdf'>下载</a></div>"
            "<a href='/side/hydj/b.pdf'>侧边栏</a>"
            "</body></html>"
        )
        found = find_attachment_link(
            "https://x/y.html",
            parse_html(html),
            suffixes=(".pdf",),
            sidebar_prefixes=("hydj",),
        )
        assert found == "https://x/files/a.pdf"

    def test_find_attachment_link_word(self) -> None:
        html = "<div class='fujian'><a href='/f/a.docx'>附件</a></div>"
        found = find_attachment_link(
            "https://x/y.html", parse_html(html), suffixes=(".doc", ".docx")
        )
        assert found == "https://x/f/a.docx"

    def test_find_attachment_link_returns_none(self) -> None:
        assert find_attachment_link("https://x/y.html", parse_html("<p>无附件</p>")) is None


class TestCsrcModels:
    def test_build_case_id(self) -> None:
        url = "https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml"
        assert build_case_id("2026-01-05", url) == "20260105_c7615688"

    def test_is_fund_by_title_tri_state(self) -> None:
        assert is_fund_by_title("关于某某基金公司的处罚") is True
        assert is_fund_by_title("关于某某期货公司的处罚") is False
        assert is_fund_by_title("关于某某公司的处罚") is None

    def test_is_fund_by_content(self) -> None:
        assert is_fund_by_content("涉及基金业务") is True
        assert is_fund_by_content("无相关内容") is False

    def test_extract_document_number(self) -> None:
        assert extract_document_number("沪〔2025〕47号 正文开始") != ""
        assert extract_document_number("没有文号的正文") == ""

    def test_extract_punished_entities(self) -> None:
        assert (
            extract_punished_entities("关于对某某基金管理有限公司采取警示函措施的决定", "")
            == "某某基金管理有限公司"
        )

    def test_is_non_case_title(self) -> None:
        assert is_non_case_title("2024年4月12日新闻发布会") is True
        assert is_non_case_title("某某主席在论坛上的主题演讲") is True
        assert is_non_case_title("关于对某某公司采取责令改正措施的决定") is False
        assert is_non_case_title("") is False


class TestCsrcDiscovery:
    """列表页链接发现：排除页面框架、列表页 URL 与新闻体裁链接。"""

    _BASE = "https://www.csrc.gov.cn/zhejiang/c103952/common_list_gd.shtml"
    _CASE_URL = "https://www.csrc.gov.cn/zhejiang/c103952/c7658145/content.shtml"

    def _parse(self, html: str) -> list[dict]:
        return _parse_list_items_from_soup(parse_html(html), self._BASE)

    def test_parses_normal_case_items(self) -> None:
        html = (
            "<ul class='list'>"
            "<li><a href='/zhejiang/c103952/c7658145/content.shtml'>"
            "关于对某某公司采取责令改正措施的决定</a>2026-09-11</li>"
            "</ul>"
        )
        items = self._parse(html)
        assert len(items) == 1
        assert items[0]["link_url"] == self._CASE_URL

    def test_skips_nav_bar_links(self) -> None:
        html = (
            "<div class='nav'><ul class='nav-bar'>"
            "<li class='xwfb'><a href='/csrc/xwfb/index.shtml'>新闻发布</a></li>"
            "</ul></div>"
            "<ul class='list'>"
            "<li><a href='/zhejiang/c103952/c7658145/content.shtml'>"
            "关于对某某公司采取责令改正措施的决定</a>2026-09-11</li>"
            "</ul>"
        )
        assert [item["link_url"] for item in self._parse(html)] == [self._CASE_URL]

    def test_skips_non_detail_urls(self) -> None:
        html = (
            "<ul class='list'>"
            "<li><a href='/csrc/xwfb/index.shtml'>新闻发布</a>2026-06-17</li>"
            "<li><a href='/zhejiang/c103952/common_list_gd.shtml'>返回列表</a>2026-06-17</li>"
            "</ul>"
        )
        assert self._parse(html) == []

    def test_skips_news_release_titles(self) -> None:
        html = (
            "<ul class='list'>"
            "<li><a href='/csrc/c100029/c7473708/content.shtml'>2024年4月12日新闻发布会</a>2024-04-12</li>"
            "<li><a href='/csrc/c106311/c7639684/content.shtml'>某某主席在论坛上的主题演讲</a>2026-06-17</li>"
            "</ul>"
        )
        assert self._parse(html) == []


class TestBureaus:
    def test_lookup_by_name(self) -> None:
        assert get_bureau_by_name("HQ") is not None
        assert get_bureau_by_name("unknown") is None

    def test_bureau_count(self) -> None:
        assert len(BUREAUS) == 37
