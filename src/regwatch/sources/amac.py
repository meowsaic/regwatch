"""AMAC 纪律处分案例采集器。

从中国证券投资基金业协会官网抓取纪律处分案例（受处分机构 / 受处分人员），
自动识别内容类型（HTML 全文 / 直接 PDF / 中间页 → PDF），提取正文后入库。

依赖全部通过参数注入：

- 网络 → :class:`~regwatch.sources.http.HttpClient`（默认共享会话）
- 页面解析 → :mod:`regwatch.sources.htmlparse`
- PDF 版式识别 → :class:`~regwatch.llm.LLMClientFactory`
- 机构类型解析 → :class:`~regwatch.services.org_type.OrgTypeService`
- 落盘 → :class:`~regwatch.db.DataStore`
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from ..clock import now_iso
from ..domain import CaseRecord, Category, Dataset
from ..logging_setup import get_logger
from .common import extract_amac_case_id, parse_date_from_text
from .htmlparse import (
    as_tag,
    extract_main_text,
    fetch_soup,
    find_attachment_link,
    parse_html,
)
from .http import close_shared_session, shared_session
from .progress import Progress, progress

logger = get_logger("sources.amac")

__all__ = [
    "CATEGORIES",
    "FetchResult",
    "collect_case_links",
    "fetch",
    "fetch_pending",
    "fetch_single",
    "get_last_quarter_range",
]

CATEGORIES: dict[str, dict[str, str]] = {
    "Institution": {"name": "受处分机构", "url": "https://www.amac.org.cn/zlgl/jlcf/scfjg/"},
    "Personnel": {"name": "受处分人员", "url": "https://www.amac.org.cn/zlgl/jlcf/scfry/"},
}

DELAY_BETWEEN_PAGES = 1.5
DELAY_BETWEEN_CASES = 2.0
LIST_PAGE_RETRIES = 3
MAX_CONSECUTIVE_PAGE_FAILURES = 3
OCR_MAX_RETRIES = 3
MIN_TEXT_LEN = 50

PDF_OCR_PROMPT = (
    "请完整、逐字逐句地识别并输出这份PDF文档的全部文字内容。"
    "不要省略任何段落，不要做归纳总结，"
    "保持原文的结构和格式，包括标题、编号、落款等。"
    "如果遇到印章或手写文字，也请尽量识别。"
)


def get_last_quarter_range() -> tuple[date, date]:
    """返回上一个季度的起止日期。"""
    today = datetime.now()
    year, quarter = today.year, (today.month - 1) // 3 + 1
    if quarter == 1:
        last_year, last_quarter = year - 1, 4
    else:
        last_year, last_quarter = year, quarter - 1
    start_month = (last_quarter - 1) * 3 + 1
    start = datetime(last_year, start_month, 1).date()
    if last_quarter == 4:
        end = datetime(last_year + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(last_year, start_month + 3, 1).date() - timedelta(days=1)
    logger.info("目标季度: %d年Q%d (%s ~ %s)", last_year, last_quarter, start, end)
    return start, end


# ──────────────────────────── PDF 版式识别 ────────────────────────────


def ocr_pdf_url(pdf_url: str, llm: Any | None = None) -> str | None:
    """把 PDF 的 URL 交给视觉模型识别，返回逐字全文。"""
    if llm is None:
        return None
    for attempt in range(1, OCR_MAX_RETRIES + 1):
        try:
            logger.info("  [PDF→文本] 请求模型识别... (尝试 %d/%d)", attempt, OCR_MAX_RETRIES)
            text = llm.client(task="vision").vision(
                pdf_url, PDF_OCR_PROMPT, max_tokens=4000, temperature=0.1
            )
            if text and text.strip():
                logger.info("  [PDF→文本] 识别完成，长度: %d 字符", len(text))
                return text
            logger.warning("  [PDF→文本] 模型返回空内容")
        except Exception as exc:
            logger.warning("  [PDF→文本] 第 %d 次调用失败: %s", attempt, exc)
            if attempt < OCR_MAX_RETRIES:
                time.sleep(3 * attempt)
    return None


# ──────────────────────────── 列表页爬取 ────────────────────────────


def collect_case_links(category_key: str, start_date: date, end_date: date) -> list[dict]:
    """从列表页收集案例链接，返回 ``[{link_url, title, date}, ...]``。"""
    base_url = CATEGORIES[category_key]["url"]
    session = shared_session()
    results: list[dict] = []
    page_index = 0
    consecutive_failures = 0

    while True:
        url = base_url + ("index.html" if page_index == 0 else f"index_{page_index}.html")
        logger.info("解析列表页 %d: %s", page_index + 1, url)

        page_ok = False
        stop = False
        for retry in range(LIST_PAGE_RETRIES):
            try:
                response = session.get(url)
                if response.status_code == 404:
                    logger.info("列表页404，翻页结束")
                    stop = True
                    page_ok = True
                    break
                response.raise_for_status()
                if response.encoding in (None, "ISO-8859-1"):
                    response.encoding = response.apparent_encoding or "utf-8"

                soup = parse_html(response.text)
                items = _select_items(soup)
                if not items:
                    logger.warning("列表页无列表结构，停止翻页")
                    stop = True
                    page_ok = True
                    break

                count = 0
                for item in items:
                    link_tag = item.find("a", href=True)
                    if link_tag is None:
                        continue
                    title = link_tag.get_text(strip=True)
                    link_url = str(link_tag.get("href") or "")
                    item_date = parse_date_from_text(item.get_text(strip=True))
                    if item_date is None or item_date > end_date:
                        continue
                    if item_date < start_date:
                        logger.info("遇到过期日期 %s，停止翻页", item_date)
                        stop = True
                        break
                    results.append({"link_url": link_url, "title": title, "date": item_date})
                    count += 1

                logger.info("  第 %d 页收集到 %d 个案例", page_index + 1, count)
                page_ok = True
                break
            except Exception as exc:
                if retry < LIST_PAGE_RETRIES - 1:
                    wait = 3 * (retry + 1)
                    logger.warning(
                        "列表页请求失败 (重试 %d/%d): %s，%d秒后重试",
                        retry + 1,
                        LIST_PAGE_RETRIES,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error("列表页处理出错 (已重试%d次): %s", LIST_PAGE_RETRIES, exc)

        if stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(
                "第 %d 页跳过 (连续失败 %d/%d)",
                page_index + 1,
                consecutive_failures,
                MAX_CONSECUTIVE_PAGE_FAILURES,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                logger.error("连续 %d 页失败，停止翻页", MAX_CONSECUTIVE_PAGE_FAILURES)
                break
        else:
            consecutive_failures = 0

        page_index += 1
        time.sleep(DELAY_BETWEEN_PAGES)

    return results


def _select_items(soup: Any) -> list[Any]:
    """按站点常见选择器定位列表条目。"""
    for selector in (
        "ul.news_list li",
        ".list-main li",
        ".yl-list-con li",
        "ul.txt-list li",
        ".list ul li",
        ".content ul li",
    ):
        found = soup.select(selector)
        if found:
            return list(found)

    main_area = as_tag(soup.find("div", class_=re.compile(r"right|main")))
    if main_area is not None:
        return [li for li in main_area.find_all("li") if len(li.get_text(strip=True)) > 10]
    return []


# ──────────────────────────── 单案例处理 ────────────────────────────


def process_case(
    link_url: str,
    title: str,
    item_date: date,
    category_key: str,
    *,
    llm: Any | None = None,
    org_type: Any | None = None,
    existing: CaseRecord | None = None,
) -> CaseRecord:
    """处理单个案例链接，返回 :class:`CaseRecord`（不落盘）。"""
    category = Category.INSTITUTION if category_key == "Institution" else Category.PERSONNEL
    base_url = CATEGORIES[category_key]["url"]
    full_link = link_url if link_url.startswith("http") else urljoin(base_url, link_url)
    case_id = existing.case_id if existing else extract_amac_case_id(full_link)

    source_type = "html"
    raw_text = ""
    pdf_url = ""

    if ".pdf" in full_link.lower():
        source_type = "pdf_direct"
        pdf_url = full_link
        logger.info("  [直接PDF] %s", title)
        raw_text = ocr_pdf_url(full_link, llm) or ""
    else:
        soup = fetch_soup(full_link)
        found = find_attachment_link(full_link, soup, suffixes=(".pdf",)) if soup else None
        pdf_url = found or ""

        if pdf_url:
            source_type = "pdf_embedded"
            logger.info("  [中间页→PDF] %s → %s", title, pdf_url)
            raw_text = ocr_pdf_url(pdf_url, llm) or ""

        if not raw_text and soup is not None:
            source_type = "html"
            raw_text = extract_main_text(soup, unwrap_inline=False) or ""

        if not raw_text:
            logger.warning("  [无内容] %s - HTML无正文且未找到可用的PDF", title)

    punished_entity = ""
    org_type_value = ""
    if org_type is not None:
        punished_entity = org_type.extract_entity(title, raw_text)
        org_type_value = org_type.resolve(title, raw_text, punished_entity)

    record = CaseRecord(
        dataset=Dataset.AMAC,
        case_id=case_id,
        source_url=full_link,
        category=category,
        source_type=source_type,
        title=title,
        date=item_date.isoformat(),
        raw_text=raw_text,
        fetch_time=now_iso(),
        ocr_success=len(raw_text) > MIN_TEXT_LEN,
        error="" if raw_text else "未能提取到有效文本",
        pdf_url=pdf_url,
        org_type=org_type_value,
        punished_entity=punished_entity,
    )
    progress(
        f"  [{'✓' if record.ocr_success else '✗'}] {case_id} | {source_type} | {len(raw_text)} 字符"
    )
    return record


# ──────────────────────────── 分类抓取 ────────────────────────────


def fetch_category(
    category_key: str,
    start_date: date,
    end_date: date,
    *,
    store: Any,
    llm: Any | None = None,
    org_type: Any | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, int]:
    """抓取一个分类下的全部案例，返回 ``(success, failed)``。"""
    config = CATEGORIES[category_key]
    logger.info("=" * 20 + f" {config['name']} " + "=" * 20)

    known = {record.case_id: record for record in _iter_existing(store, category_key)}
    links = collect_case_links(category_key, start_date, end_date)
    logger.info("列表页收集到 %d 个案例", len(links))

    to_process: list[dict] = []
    for link in links:
        case_id = extract_amac_case_id(link["link_url"])
        existing = known.get(case_id)
        # 已成功且非 HTML 来源的直接跳过；HTML 来源可能漏了 PDF 附件，重检一次
        if existing is not None and existing.ocr_success and existing.source_type != "html":
            continue
        to_process.append(link)

    if not to_process:
        logger.info("%s: 无待处理案例", config["name"])
        return 0, 0

    logger.info("待处理 %d 个（库内已有 %d 个）", len(to_process), len(known))
    tracker = Progress(on_progress)
    tracker.start(len(to_process))

    success = failed = 0
    for item in to_process:
        title = item.get("title", "")
        tracker.advance(message=title)
        try:
            case_id = extract_amac_case_id(item["link_url"])
            record = process_case(
                item["link_url"],
                title,
                item["date"],
                category_key,
                llm=llm,
                org_type=org_type,
                existing=known.get(case_id),
            )
            store.cases.upsert(record)
            if record.ocr_success:
                success += 1
            else:
                failed += 1
        except Exception as exc:
            logger.error("  处理失败: %s", exc)
            failed += 1
        if DELAY_BETWEEN_CASES:
            time.sleep(DELAY_BETWEEN_CASES)

    logger.info("%s: 本次成功 %d，失败 %d", config["name"], success, failed)
    return success, failed


def _iter_existing(store: Any, category_key: str) -> list[CaseRecord]:
    """列出库内该分类已有案例（不含正文）。"""
    category = Category.INSTITUTION if category_key == "Institution" else Category.PERSONNEL
    records = []
    for case_id in store.cases.ids(Dataset.AMAC):
        record = store.cases.get(Dataset.AMAC, case_id)
        if record is not None and record.category is category:
            records.append(record)
    return records


# ──────────────────────────── 对外抓取 API ────────────────────────────


@dataclass
class FetchResult:
    """一次抓取任务的汇总结果。"""

    dataset: str = "amac"
    success: int = 0
    failed: int = 0
    detail: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.success + self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "success": self.success,
            "failed": self.failed,
            "total": self.total,
            "detail": self.detail,
        }


def fetch(
    start_date: date | None = None,
    end_date: date | None = None,
    categories: list[str] | None = None,
    *,
    store: Any = None,
    llm: Any | None = None,
    org_type: Any | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """抓取指定日期范围内的 AMAC 纪律处分案例。

    Args:
        start_date: 起始日期（含）；与 ``end_date`` 任一为空时回落到上一季度。
        end_date: 结束日期（含）。
        categories: ``Institution`` / ``Personnel``；默认全部。
        store: 数据仓储门面。
        llm: 模型客户端工厂（PDF 版式识别用）。
        org_type: 机构类型服务（可选，用于回填 ``org_type`` / ``punished_entity``）。
        on_progress: 进度回调 ``(已处理, 总数, 标题)``。
    """
    if store is None:
        from ..services import get_services

        services = get_services()
        store, llm, org_type = services.store, services.llm, services.org_type

    if start_date is None or end_date is None:
        default_start, default_end = get_last_quarter_range()
        start_date = start_date or default_start
        end_date = end_date or default_end

    selected = [item for item in (categories or list(CATEGORIES)) if item in CATEGORIES]
    if not selected:
        selected = list(CATEGORIES)

    result = FetchResult()
    started = time.time()
    logger.info("=" * 20 + " AMAC 案例抓取 " + "=" * 20)
    logger.info("日期范围: %s ~ %s | 分类: %s", start_date, end_date, "、".join(selected))

    try:
        for category_key in selected:
            success, failed = fetch_category(
                category_key,
                start_date,
                end_date,
                store=store,
                llm=llm,
                org_type=org_type,
                on_progress=on_progress,
            )
            result.success += success
            result.failed += failed
            result.detail[category_key] = {"success": success, "failed": failed}
    finally:
        close_shared_session()
        store.touch()

    logger.info(
        "AMAC 抓取完成：成功 %d，失败 %d，耗时 %.1f 秒",
        result.success,
        result.failed,
        time.time() - started,
    )
    return result


def fetch_pending(
    categories: list[str] | None = None,
    *,
    store: Any = None,
    llm: Any | None = None,
    org_type: Any | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """断点续传：扫描全部区间，库内已有正文的案例自动跳过。"""
    return fetch(
        start_date=datetime.min.date(),
        end_date=datetime.max.date(),
        categories=categories,
        store=store,
        llm=llm,
        org_type=org_type,
        on_progress=on_progress,
    )


def fetch_single(
    url: str,
    category: str = "Institution",
    *,
    store: Any = None,
    llm: Any | None = None,
    org_type: Any | None = None,
) -> CaseRecord | None:
    """抓取单个案例 URL 并入库。"""
    if store is None:
        from ..services import get_services

        services = get_services()
        store, llm, org_type = services.store, services.llm, services.org_type

    try:
        record = process_case(
            url, "手动输入案例", datetime.now().date(), category, llm=llm, org_type=org_type
        )
        store.cases.upsert(record)
        store.touch()
        return record
    finally:
        close_shared_session()
