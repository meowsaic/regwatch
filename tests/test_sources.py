"""采集层测试：共享工具、HTML 解析、CSRC 纯函数与来源配置。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from regwatch.db import DataStore
from regwatch.domain import CaseRecord, CaseStatus, Dataset
from regwatch.sources import amac, csrc
from regwatch.sources.bureaus import BUREAUS, Bureau, discover_penalty_url, get_bureau_by_name
from regwatch.sources.common import (
    FETCH_OVERLAP_DAYS,
    default_fetch_range,
    extract_amac_case_id,
    extract_csrc_case_id,
    parse_date_from_text,
    sanitize_filename,
)
from regwatch.sources.csrc import runner
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
from regwatch.sources.http import DEFAULT_HEADERS, DEFAULT_USER_AGENT


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

    def test_default_headers_include_user_agent(self) -> None:
        # CSRC 部分地方局首页对无 UA 的请求返回 403（甘肃/贵州实测），
        # DEFAULT_HEADERS 必须带浏览器 UA，否则 penalty 栏目发现失败。
        assert DEFAULT_HEADERS["User-Agent"] == DEFAULT_USER_AGENT


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
        # 标题含"基金"的行政许可公示表曾绕过正文确认入库，必须按体裁排除
        assert is_non_case_title("江西辖区证券基金机构行政许可申请受理及审核情况公示表") is True
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


def _seed(store: DataStore, dataset: Dataset, case_date: str) -> None:
    store.cases.upsert(
        CaseRecord(
            dataset=dataset,
            case_id=f"{dataset.value}-{case_date}",
            title="测试案例",
            date=case_date,
            raw_text="x" * 60,
        )
    )


class TestDefaultFetchRange:
    """增量抓取默认区间：上次覆盖日期 - 回看天数 ~ 今天。"""

    def test_empty_db_falls_back_to_first_run_start(self, store: DataStore) -> None:
        start, end = default_fetch_range(
            store, Dataset.AMAC, first_run_start=date(2025, 7, 1), today=date(2026, 9, 12)
        )
        assert (start, end) == (date(2025, 7, 1), date(2026, 9, 12))

    def test_starts_from_latest_case_date_minus_overlap(self, store: DataStore) -> None:
        _seed(store, Dataset.CSRC, "2026-09-01")
        start, end = default_fetch_range(
            store, Dataset.CSRC, first_run_start=date(2022, 1, 1), today=date(2026, 9, 12)
        )
        assert start == date(2026, 9, 1) - timedelta(days=FETCH_OVERLAP_DAYS)
        assert end == date(2026, 9, 12)

    def test_datasets_do_not_cross_contaminate(self, store: DataStore) -> None:
        _seed(store, Dataset.CSRC, "2026-09-10")
        start, _ = default_fetch_range(
            store, Dataset.AMAC, first_run_start=date(2025, 7, 1), today=date(2026, 9, 12)
        )
        assert start == date(2025, 7, 1)

    def test_amac_first_run_start_is_previous_quarter(self) -> None:
        # 9 月属于 Q3，「上一季度第一天」是 4 月 1 日
        assert amac.first_run_start(date(2026, 9, 12)) == date(2026, 4, 1)
        assert amac.first_run_start(date(2026, 1, 5)) == date(2025, 10, 1)


class TestFetchDefaults:
    """抓取入口不传日期时走增量默认区间，结束日期为调用当天。"""

    def test_amac_fetch_uses_incremental_range(self, store: DataStore, monkeypatch) -> None:
        _seed(store, Dataset.AMAC, "2026-09-01")
        captured: list[tuple[date, date]] = []

        def fake_fetch_category(category_key, start_date, end_date, **kwargs):
            captured.append((start_date, end_date))
            return 0, 0

        monkeypatch.setattr(amac, "fetch_category", fake_fetch_category)
        amac.fetch(store=store, categories=["Institution"])

        assert captured == [
            (date(2026, 9, 1) - timedelta(days=FETCH_OVERLAP_DAYS), datetime.now().date())
        ]

    def test_csrc_fetch_uses_incremental_range(self, store: DataStore, monkeypatch) -> None:
        _seed(store, Dataset.CSRC, "2026-09-01")
        captured: list[tuple[date, date]] = []

        def fake_fetch_source(bureau, case_type, start_date, end_date, **kwargs):
            captured.append((start_date, end_date))
            return 0, 0, 0

        monkeypatch.setattr("regwatch.sources.csrc.runner.fetch_source", fake_fetch_source)
        csrc.fetch(store=store, bureaus=["HQ"], case_types=["penalty"])

        assert captured == [
            (date(2026, 9, 1) - timedelta(days=FETCH_OVERLAP_DAYS), datetime.now().date())
        ]

    def test_explicit_dates_win_over_incremental_default(
        self, store: DataStore, monkeypatch
    ) -> None:
        _seed(store, Dataset.AMAC, "2026-09-01")
        captured: list[tuple[date, date]] = []

        def fake_fetch_category(category_key, start_date, end_date, **kwargs):
            captured.append((start_date, end_date))
            return 0, 0

        monkeypatch.setattr(amac, "fetch_category", fake_fetch_category)
        amac.fetch(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            store=store,
            categories=["Institution"],
        )
        assert captured == [(date(2026, 1, 1), date(2026, 3, 31))]


class TestBureaus:
    def test_lookup_by_name(self) -> None:
        assert get_bureau_by_name("HQ") is not None
        assert get_bureau_by_name("unknown") is None

    def test_bureau_count(self) -> None:
        assert len(BUREAUS) == 37

    def test_all_bureaus_have_penalty_channel(self) -> None:
        # 37 个来源全部离线配置行政处罚栏目，抓取时不再依赖首页自动发现
        missing = [b.name_en for b in BUREAUS if not b.penalty_url or not b.penalty_channelid]
        assert missing == []

    def test_discover_penalty_url_sends_user_agent(self, monkeypatch) -> None:
        """行政处罚栏目发现必须带 UA，否则部分局首页 403。"""
        captured: dict[str, object] = {}

        class _FakeResponse:
            status_code = 200
            text = "<html><body></body></html>"
            encoding = "utf-8"
            apparent_encoding = "utf-8"

            def raise_for_status(self) -> None:
                return None

        def fake_get(url, headers=None, timeout=None):
            captured["headers"] = headers
            captured["url"] = url
            return _FakeResponse()

        monkeypatch.setattr("regwatch.sources.bureaus.requests.get", fake_get)
        # 构造一个未配置行政处罚栏目的临时来源，强制走首页发现路径
        bureau = Bureau(
            name_cn="测试局",
            name_en="TestBureau",
            path_code="c999999",
            measure_url="https://www.csrc.gov.cn/csrc/c999999/common_list_gd.shtml",
            site_path="beijing",
        )
        assert discover_penalty_url(bureau) is None  # 页面里没有栏目链接
        headers = captured["headers"] or {}
        assert "User-Agent" in headers
        assert str(captured["url"]).endswith("/beijing/index.shtml")


class TestCsrcFailedRetry:
    """抓取失败（无正文）的案例允许重试；已有正文或已跳过的不重复处理。"""

    _URL = "https://www.csrc.gov.cn/csrc/c106065/c7650001/content.shtml"
    _TITLE = "关于对某某基金管理有限公司采取出具警示函措施的决定"

    def _link(self) -> dict:
        return {"link_url": self._URL, "title": self._TITLE, "date": date(2026, 9, 1)}

    def _seed(
        self,
        store: DataStore,
        *,
        raw_text: str,
        status: CaseStatus = CaseStatus.PENDING,
    ) -> str:
        case_id = build_case_id("2026-09-01", self._URL)
        store.cases.upsert(
            CaseRecord(
                dataset=Dataset.CSRC,
                case_id=case_id,
                title=self._TITLE,
                date="2026-09-01",
                status=status,
                raw_text=raw_text,
            )
        )
        return case_id

    def _run(self, store: DataStore, monkeypatch) -> list[dict]:
        calls: list[dict] = []

        def fake_process_case(**kwargs):
            calls.append(kwargs)
            return None  # 视为「非基金」跳过，不写库

        monkeypatch.setattr(
            "regwatch.sources.csrc.runner.collect_measure_links",
            lambda *args, **kwargs: [self._link()],
        )
        monkeypatch.setattr("regwatch.sources.csrc.runner.process_case", fake_process_case)
        bureau = get_bureau_by_name("HQ")
        assert bureau is not None
        runner.fetch_source(
            bureau,
            "measure",
            date(2026, 8, 1),
            date(2026, 9, 12),
            store=store,
            concurrency=1,
        )
        return calls

    def test_missing_body_case_is_retried(self, store: DataStore, monkeypatch) -> None:
        self._seed(store, raw_text="")
        assert len(self._run(store, monkeypatch)) == 1

    def test_case_with_body_is_skipped(self, store: DataStore, monkeypatch) -> None:
        self._seed(store, raw_text="x" * 60)
        assert self._run(store, monkeypatch) == []

    def test_skipped_placeholder_is_not_retried(self, store: DataStore, monkeypatch) -> None:
        self._seed(store, raw_text="", status=CaseStatus.SKIPPED)
        assert self._run(store, monkeypatch) == []

    def test_content_filtered_case_is_persisted_and_not_retried(
        self, store: DataStore, monkeypatch
    ) -> None:
        """有正文但判定非基金：落盘 SKIPPED 占位记录，下次重扫不再重复下载。"""
        from regwatch.sources.csrc.models import CaseData

        def fake_process_case(**_kwargs):
            return CaseData(
                case_id=build_case_id("2026-09-01", self._URL),
                source_url=self._URL,
                case_type="measure",
                bureau="HQ",
                title=self._TITLE,
                date="2026-09-01",
                raw_text="某上市公司信息披露违规的处罚正文" * 5,
                is_fund_related=False,
                fund_evidence="",
            )

        monkeypatch.setattr(
            "regwatch.sources.csrc.runner.collect_measure_links",
            lambda *args, **kwargs: [self._link()],
        )
        monkeypatch.setattr("regwatch.sources.csrc.runner.process_case", fake_process_case)
        bureau = get_bureau_by_name("HQ")
        assert bureau is not None

        runner.fetch_source(
            bureau,
            "measure",
            date(2026, 8, 1),
            date(2026, 9, 12),
            store=store,
            concurrency=1,
        )

        case_id = build_case_id("2026-09-01", self._URL)
        record = store.cases.get(Dataset.CSRC, case_id)
        assert record is not None
        assert record.status == CaseStatus.SKIPPED
        assert record.status_note == "内容判定非基金"
        assert store.cases.get_body(Dataset.CSRC, case_id) == ""  # 占位记录不写正文

        # 二次抓取：SKIPPED 命中跳过，process_case 不再被调用
        calls: list[dict] = []

        def counting_fake(**kwargs):
            calls.append(kwargs)
            return fake_process_case(**kwargs)

        monkeypatch.setattr("regwatch.sources.csrc.runner.process_case", counting_fake)
        runner.fetch_source(
            bureau,
            "measure",
            date(2026, 8, 1),
            date(2026, 9, 12),
            store=store,
            concurrency=1,
        )
        assert calls == []


class TestIdsMissingBody:
    def test_separates_failed_from_successful(self, store: DataStore) -> None:
        store.cases.upsert(CaseRecord(dataset=Dataset.CSRC, case_id="ok", raw_text="x" * 60))
        store.cases.upsert(CaseRecord(dataset=Dataset.CSRC, case_id="bad"))
        assert store.cases.ids_missing_body(Dataset.CSRC) == ["bad"]
        assert store.cases.ids_missing_body(Dataset.AMAC) == []


# ──────────────────────────── 软 404 防护与 API 优先 ────────────────────────────


_HOMEPAGE_HTML = (
    "<html><head><title>中国证券监督管理委员会</title></head><body>"
    "<ul><li><a href='/xiamen/c104096/c7658058/content.shtml'>"
    "厦门证监局行政许可事项审核过程公示表</a> 2026-09-11</li></ul>"
    "</body></html>"
)


def _list_page_html(*rows: tuple[str, str]) -> str:
    """构造监管措施列表页 HTML；rows 为 (标题, 日期)。"""
    lis = "".join(
        f"<li><a href='/beijing/c103542/{idx}/content.shtml'>{title}</a> {row_date}</li>"
        for idx, (title, row_date) in enumerate(rows, start=100)
    )
    return f"<html><head><title>北京</title></head><body><ul class='list'>{lis}</ul></body></html>"


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, json_data: object = None) -> None:
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"
        self._json_data = json_data
        self.headers = (
            {"Content-Type": "application/json"}
            if json_data is not None
            else {"Content-Type": "text/html"}
        )

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self) -> object:
        assert self._json_data is not None
        return self._json_data


class _FakeSession:
    """按顺序回放响应的假会话。"""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        if self._responses:
            return self._responses.pop(0)
        # 响应耗尽后一直返回软404首页（复现官网越界翻页行为）
        return _FakeResponse(text=_HOMEPAGE_HTML)


class TestSoft404Guard:
    """软404首页识别与翻页终止（官网越界翻页返回 HTTP 200 首页，曾导致死循环）。"""

    def _patch_sleep(self, monkeypatch) -> None:
        monkeypatch.setattr("regwatch.sources.csrc.discovery.time.sleep", lambda _s: None)

    def test_is_homepage_page(self) -> None:
        from regwatch.sources.csrc.discovery import _is_homepage_page

        assert _is_homepage_page(parse_html(_HOMEPAGE_HTML))
        assert not _is_homepage_page(parse_html(_list_page_html(("对某公司的决定", "2026-08-01"))))

    def test_measure_overrange_homepage_stops_without_junk(self, monkeypatch) -> None:
        """越界页返回首页：应终止翻页且不把首页垃圾条目当作案例（死循环回归测试）。"""
        from regwatch.sources.csrc import discovery

        self._patch_sleep(monkeypatch)
        page1 = _FakeResponse(text=_list_page_html(("对某基金销售公司的决定", "2026-08-01")))
        session = _FakeSession([page1])
        monkeypatch.setattr(discovery, "shared_session", lambda: session)
        bureau = get_bureau_by_name("Beijing")
        assert bureau is not None

        items = discovery.collect_measure_links(bureau, date(2026, 1, 1), date(2026, 12, 31))

        assert [str(i["link_url"]) for i in items] == [
            "https://www.csrc.gov.cn/beijing/c103542/100/content.shtml"
        ]
        # 翻页在第 2 页（首页软404）终止，而不是无限翻下去
        assert len(session.calls) <= 4

    def test_measure_date_stop_before_soft404(self, monkeypatch) -> None:
        """列表末尾有早于起始日期的条目时，应在软404区之前日期停止。"""
        from regwatch.sources.csrc import discovery

        self._patch_sleep(monkeypatch)
        page1 = _FakeResponse(
            text=_list_page_html(
                ("对某基金销售公司的决定", "2026-08-01"),
                ("对某基金销售公司的警告", "2021-06-01"),
            )
        )
        session = _FakeSession([page1])
        monkeypatch.setattr(discovery, "shared_session", lambda: session)
        bureau = get_bureau_by_name("Beijing")
        assert bureau is not None

        items = discovery.collect_measure_links(bureau, date(2022, 1, 1), date(2026, 12, 31))

        assert len(items) == 1
        assert len(session.calls) == 1

    def test_measure_duplicate_page_stops(self, monkeypatch) -> None:
        """某页条目与之前完全重复（软404首页反复注入同一垃圾条目的特征）时终止。

        首个含新 URL 的页会被照常收录（标题识别是第一道防线），
        重复页检测保证不会再无限翻下去。
        """
        from regwatch.sources.csrc import discovery

        self._patch_sleep(monkeypatch)
        junk_page = _FakeResponse(
            text="<html><head><title>其他</title></head><body>"
            "<ul class='list'><li><a href='/x/c1/c9/content.shtml'>"
            "关于某基金销售公司的说明</a> 2026-09-11</li></ul>"
            "</body></html>"
        )
        session = _FakeSession(
            [
                _FakeResponse(text=_list_page_html(("对某基金销售公司的决定", "2026-08-01"))),
                junk_page,
                junk_page,
                junk_page,
            ]
        )
        monkeypatch.setattr(discovery, "shared_session", lambda: session)
        bureau = get_bureau_by_name("Beijing")
        assert bureau is not None

        items = discovery.collect_measure_links(bureau, date(2026, 1, 1), date(2026, 12, 31))

        assert len(items) == 2
        # 第 3 页起整页重复，翻页终止而不是无限请求
        assert len(session.calls) == 3


class TestSearchListApiSemantics:
    """_try_searchlist_api 返回语义：None=API 不可用；[]=API 正常但范围内无案例。"""

    def _api_json(self, *items: dict) -> _FakeResponse:
        return _FakeResponse(
            json_data={
                "channelName": "行政处罚决定",
                "data": {"total": len(items), "results": list(items)},
            }
        )

    @staticmethod
    def _item(title: str, date_str: str, url: str) -> dict:
        return {"title": title, "url": url, "publishedTimeStr": date_str}

    def _run(self, monkeypatch, responses: list[_FakeResponse], **kwargs: object):
        from regwatch.sources.csrc import discovery

        monkeypatch.setattr("regwatch.sources.csrc.discovery.time.sleep", lambda _s: None)
        session = _FakeSession(responses)
        monkeypatch.setattr(discovery, "shared_session", lambda: session)
        bureau = get_bureau_by_name("Beijing")
        assert bureau is not None
        return discovery._try_searchlist_api(
            bureau,
            kwargs.pop("start", date(2022, 1, 1)),
            kwargs.pop("end", date(2026, 12, 31)),
        )

    def test_missing_channelid_returns_none(self, monkeypatch) -> None:
        from regwatch.sources.csrc import discovery

        monkeypatch.setattr("regwatch.sources.csrc.discovery.time.sleep", lambda _s: None)
        bureau = Bureau(
            name_cn="测试局", name_en="Test", path_code="c1", measure_url="", site_path="test"
        )
        assert bureau.penalty_channelid == ""
        result = discovery._try_searchlist_api(bureau, date(2022, 1, 1), date(2026, 12, 31))
        assert result is None

    def test_first_page_failure_returns_none(self, monkeypatch) -> None:
        assert self._run(monkeypatch, [_FakeResponse(status_code=503)]) is None

    def test_api_ok_but_empty_range_returns_empty_list(self, monkeypatch) -> None:
        """范围内无案例是确定性结果（空列表），不应误判为 API 失败。"""
        result = self._run(
            monkeypatch,
            [
                self._api_json(
                    self._item(
                        "某处罚决定", "2021-06-01", "//www.csrc.gov.cn/beijing/c1/c9/content.shtml"
                    )
                )
            ],
        )
        assert result == []

    def test_items_collected_and_page2_failure_keeps_results(self, monkeypatch) -> None:
        good_url = "//www.csrc.gov.cn/beijing/c105546/c7657268/content.shtml"
        responses = [
            self._api_json(
                self._item("某处罚决定", "2026-08-01 10:00", good_url),
                self._item(
                    "旧处罚决定",
                    "2021-01-01 10:00",
                    "//www.csrc.gov.cn/beijing/c1/c8/content.shtml",
                ),
            ),
            _FakeResponse(status_code=503),  # 本不会到达：日期停止在第 1 页触发
        ]
        result = self._run(monkeypatch, responses)
        assert result is not None
        assert len(result) == 1
        assert (
            result[0]["link_url"]
            == "https://www.csrc.gov.cn/beijing/c105546/c7657268/content.shtml"
        )

    def test_collect_penalty_prefers_api(self, monkeypatch) -> None:
        """penalty 抓取应优先走 searchList API，而不是等 HTML 解析失败后才兜底。"""
        from datetime import date as _date

        from regwatch.sources.csrc import discovery

        monkeypatch.setattr("regwatch.sources.csrc.discovery.time.sleep", lambda _s: None)
        calls: list[str] = []

        def fake_api(bureau, start, end, *, penalty_url="", channelid=""):
            calls.append(channelid)
            return [
                {
                    "link_url": "https://www.csrc.gov.cn/x/content.shtml",
                    "title": "某处罚决定",
                    "date": _date(2026, 8, 1),
                }
            ]

        session = _FakeSession([])  # 若误走 HTML 翻页会拿到软404首页
        monkeypatch.setattr(discovery, "shared_session", lambda: session)
        monkeypatch.setattr(discovery, "_try_searchlist_api", fake_api)
        bureau = get_bureau_by_name("Beijing")
        assert bureau is not None

        items = discovery.collect_penalty_links(bureau, date(2022, 1, 1), date(2026, 12, 31))

        assert len(items) == 1
        assert calls == [bureau.penalty_channelid]
        assert session.calls == []  # 未发起任何 HTML 列表页请求
