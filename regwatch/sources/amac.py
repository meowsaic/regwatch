"""AMAC 纪律处分案例采集器。

从中国证券投资基金业协会官网抓取纪律处分案例（受处分机构 / 受处分人员），
自动识别内容类型（HTML 全文 / 直接 PDF / 中间页→PDF），提取正文后落库为 JSON，
支持增量抓取与断点续传。

本模块只负责「抓取编排」，其余能力均复用包内统一实现：

- 模型调用 → :mod:`regwatch.llm`
- 机构登记类型解析与回填 → :mod:`regwatch.org_type`
- 索引、缓存与落盘 → :mod:`regwatch.storage`
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter, Retry

from ..config import Config, get_config
from ..llm import LLMError, get_llm
from ..logutil import configure_logging, get_logger
from ..org_type import extract_punished_entity, resolve_org_type
from ..storage import AmacIndex, OrgTypeCache, write_json
from ._html import as_tag, attr_str

logger = get_logger("sources.amac")

CATEGORIES = {
    "Institution": {
        "name": "受处分机构",
        "url": "https://www.amac.org.cn/zlgl/jlcf/scfjg/",
    },
    "Personnel": {
        "name": "受处分人员",
        "url": "https://www.amac.org.cn/zlgl/jlcf/scfry/",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

TIMEOUT = 30
MAX_RETRIES = 3
DELAY_BETWEEN_PAGES = 1.5
DELAY_BETWEEN_API_CALLS = 2
OCR_MAX_RETRIES = 3
LIST_PAGE_RETRIES = 3
MAX_CONSECUTIVE_PAGE_FAILURES = 3


# 日志：统一走 regwatch.logutil（logger 定义见模块头部），不再自建处理器。


# ──────────────────────────── 数据模型 ────────────────────────────


@dataclass
class CaseData:
    case_id: str
    source_url: str
    source_type: str  # "html" | "pdf_direct" | "pdf_embedded"
    category: str  # "scfjg" | "scfry"
    title: str
    date: str
    raw_text: str = ""
    fetch_time: str = ""
    ocr_success: bool = False
    error: str = ""
    pdf_url: str = ""
    org_type: str = ""
    punished_entity: str = ""


# ──────────────────────────── 案例索引 ────────────────────────────

# 索引实现已统一收敛到 regwatch.storage，这里保留别名以兼容原有调用点。
CaseIndex = AmacIndex
# ──────────────────────────── HTTP Session ────────────────────────────


class SessionManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get(self, url, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return self.session.get(url, **kwargs)

    def close(self):
        if self.session:
            self.session.close()
            SessionManager._instance = None


# 模型调用已统一收敛到 regwatch.llm，不再维护 provider 分支与客户端缓存。


# ──────────────────────────── 工具函数 ────────────────────────────


def extract_id_from_url(url: str) -> str:
    m = re.search(r"(P\d{20,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/t(\d+_\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{6}/[^/]+)$", url)
    if m:
        return re.sub(r"[^a-zA-Z0-9]", "_", m.group(1))
    return re.sub(r"[^a-zA-Z0-9]", "_", url.split("/")[-1])[:60]


def parse_date_from_text(text: str) -> date | None:
    m = re.search(r"(\d{4})\s*-\s*(\d{2})\s*-\s*(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    return None


def get_last_quarter_range() -> tuple[date, date]:
    today = datetime.now()
    y, q = today.year, (today.month - 1) // 3 + 1
    if q == 1:
        ly, lq = y - 1, 4
    else:
        ly, lq = y, q - 1
    start_m = (lq - 1) * 3 + 1
    start = datetime(ly, start_m, 1).date()
    if lq == 4:
        end = datetime(ly + 1, 1, 1).date() - timedelta(days=1)
    else:
        end = datetime(ly, start_m + 3, 1).date() - timedelta(days=1)
    logger.info(f"目标季度: {ly}年Q{lq} ({start} ~ {end})")
    return start, end


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


# ──────────────────────────── HTML 页面获取 ────────────────────────────


def fetch_html_page(html_url: str) -> BeautifulSoup | None:
    """获取HTML页面并返回BeautifulSoup对象，供后续提取文本和PDF链接共用"""
    try:
        session = SessionManager()
        resp = session.get(html_url)
        resp.raise_for_status()
        if resp.encoding == "ISO-8859-1":
            resp.encoding = resp.apparent_encoding
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"HTML页面获取失败 {html_url}: {e}")
        return None


# ──────────────────────────── HTML 正文提取 ────────────────────────────


def extract_text_from_html(html_url: str, soup: BeautifulSoup | None = None) -> str | None:
    """从HTML详情页提取纪律处分决定书正文"""
    try:
        if soup is None:
            soup = fetch_html_page(html_url)
        if soup is None:
            return None

        content_area = as_tag(
            soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="Custom_UnionStyle")
            or soup.find("div", class_=re.compile(r"detail|article|content|text", re.I))
            or soup.find("div", id=re.compile(r"detail|article|content", re.I))
            or soup.find("div", class_="TRAIS-info-content")
        )
        if not content_area:
            content_area = as_tag(soup.find("div", class_=re.compile(r"right|main")))

        if content_area:
            for tag in content_area.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = content_area.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text) > 100:
                return text

        body_text = soup.get_text(separator="\n", strip=True)
        body_text = re.sub(r"\n{3,}", "\n\n", body_text)
        if len(body_text) > 200:
            return body_text

        return None
    except Exception as e:
        logger.warning(f"HTML正文提取失败 {html_url}: {e}")
        return None


# ──────────────────────────── 中间页→PDF 链接发现 ────────────────────────────


def find_pdf_link_in_page(page_url: str, soup: BeautifulSoup | None = None) -> str | None:
    """从HTML页查找内嵌的PDF链接（优先查找附件下载区，避免误取侧边栏链接）"""
    try:
        if soup is None:
            soup = fetch_html_page(page_url)
        if soup is None:
            return None

        # 1) 优先在 class="attachment-down" 的容器中查找
        for container in soup.find_all("div", class_="attachment-down"):
            for link in container.find_all("a", href=True):
                href = attr_str(link, "href")
                if ".pdf" in href.lower():
                    pdf_url = urljoin(page_url, href)
                    logger.info(f"  [附件区PDF] {pdf_url}")
                    return pdf_url

        # 2) 在包含"附件"文字的标题/段落附近查找
        for heading in soup.find_all(string=re.compile(r"附件.*下载|下载.*附件")):
            parent_div = as_tag(heading.find_parent("div"))
            if parent_div:
                for link in parent_div.find_all("a", href=True):
                    href = attr_str(link, "href")
                    if ".pdf" in href.lower():
                        pdf_url = urljoin(page_url, href)
                        logger.info(f"  [附件区PDF] {pdf_url}")
                        return pdf_url

        # 3) 在 class 含 fujian/attachment/download 的容器中查找
        for container in soup.find_all(
            "div", class_=re.compile(r"fujian|attachment|download|accessory", re.I)
        ):
            for link in container.find_all("a", href=True):
                href = attr_str(link, "href")
                if ".pdf" in href.lower():
                    pdf_url = urljoin(page_url, href)
                    logger.info(f"  [下载区PDF] {pdf_url}")
                    return pdf_url

        # 4) 全页面查找，但过滤掉侧边栏链接（路径含 hydj/xwfb/hdjl/hyyj/sjtj 等非纪律处分路径）
        sidebar_prefixes = ("hydj", "xwfb", "hdjl", "hyyj", "sjtj")
        for link in soup.find_all("a", href=True):
            href = attr_str(link, "href")
            if ".pdf" not in href.lower():
                continue
            parts = href.replace("\\", "/").split("/")
            if any(p in parts for p in sidebar_prefixes):
                continue
            pdf_url = urljoin(page_url, href)
            logger.info(f"  [页面PDF] {pdf_url}")
            return pdf_url

        # 5) iframe / embed
        iframe = as_tag(soup.find("iframe", src=True))
        iframe_src = attr_str(iframe, "src")
        if iframe and ".pdf" in iframe_src.lower():
            return urljoin(page_url, iframe_src)

        for embed in soup.find_all("embed", src=True):
            embed_src = attr_str(embed, "src")
            if ".pdf" in embed_src.lower():
                return urljoin(page_url, embed_src)

        return None
    except Exception as e:
        logger.warning(f"PDF链接查找失败 {page_url}: {e}")
        return None


# ──────────────────────────── 机构类型（复用统一实现） ────────────────────────────

# 机构名称提取、登记类型查询与四级降级解析统一在 regwatch.org_type 中实现：
#   ORG_TYPE_VALUES / extract_org_name_from_title / extract_org_type_from_text
#   / extract_full_org_name_from_text / query_org_type_from_amac
#   / query_org_type_from_amac_cancelled / extract_punished_entity / resolve_org_type
# 本模块通过 extract_punished_entity / resolve_org_type 调用。


# ──────────────────────────── PDF → 文本 (直接传URL给模型) ────────────────────────────

PDF_OCR_PROMPT = (
    "请完整、逐字逐句地识别并输出这份PDF文档的全部文字内容。"
    "不要省略任何段落，不要做归纳总结，"
    "保持原文的结构和格式，包括标题、编号、落款等。"
    "如果遇到印章或手写文字，也请尽量识别。"
)


def extract_text_from_pdf_url(pdf_url: str) -> str | None:
    """把 PDF 的 URL 交给视觉模型识别，返回逐字全文。

    使用 ``vision`` 任务绑定的模型；模型未单独配置视觉能力时会回落到文本模型。
    失败自动退避重试 ``OCR_MAX_RETRIES`` 次。
    """
    for attempt in range(OCR_MAX_RETRIES):
        try:
            logger.info("  [PDF→文本] 请求模型识别... (尝试 %d/%d)", attempt + 1, OCR_MAX_RETRIES)
            client = get_llm(task="vision")
            text = client.vision(pdf_url, PDF_OCR_PROMPT, max_tokens=4000, temperature=0.1)
            if text and text.strip():
                logger.info("  [PDF→文本] 识别完成，长度: %d 字符", len(text))
                return text
            logger.warning("  [PDF→文本] 模型返回空内容")
        except LLMError as exc:
            logger.warning("  [PDF→文本] 第 %d 次调用失败: %s", attempt + 1, exc)
        except Exception as exc:
            logger.warning("  [PDF→文本] 第 %d 次异常: %s", attempt + 1, exc)

        if attempt < OCR_MAX_RETRIES - 1:
            wait = 3 * (attempt + 1)
            logger.info("  [PDF→文本] %d 秒后重试...", wait)
            time.sleep(wait)

    return None


# ──────────────────────────── 单案例处理 ────────────────────────────


def process_case(
    link_url: str,
    raw_title: str,
    item_date: date,
    category_key: str,
    output_dir: Path,
    org_type_cache: OrgTypeCache | None = None,
) -> CaseData | None:
    """处理单个案例链接，返回CaseData或None"""

    case_id = extract_id_from_url(link_url)
    json_path = output_dir / f"{case_id}.json"

    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            existing = json.load(f)
        existing_case = CaseData(**existing)
        if existing_case.ocr_success and existing_case.source_type != "html":
            logger.info(f"  [跳过] 已存在且成功: {case_id}")
            return existing_case
        elif existing_case.ocr_success and existing_case.source_type == "html":
            logger.info(f"  [重检] HTML类型可能遗漏PDF附件: {case_id}")
        else:
            logger.info(f"  [重试] 删除之前失败的记录: {case_id} ({existing_case.error})")
            json_path.unlink()

    full_link = (
        link_url
        if link_url.startswith("http")
        else urljoin(CATEGORIES[category_key]["url"], link_url)
    )

    source_type = "unknown"
    raw_text = None
    pdf_url = ""

    if ".pdf" in full_link.lower():
        source_type = "pdf_direct"
        pdf_url = full_link
        logger.info(f"  [直接PDF] {raw_title}")
        raw_text = extract_text_from_pdf_url(full_link)

    else:
        soup = fetch_html_page(full_link)
        pdf_url = find_pdf_link_in_page(full_link, soup=soup) or ""

        if pdf_url:
            source_type = "pdf_embedded"
            logger.info(f"  [中间页→PDF] {raw_title} → {pdf_url}")
            pdf_text = extract_text_from_pdf_url(pdf_url)
            if pdf_text:
                raw_text = pdf_text
                logger.info(f"  [PDF全文] {raw_title} (PDF {len(pdf_text)} 字符)")
            else:
                html_text = extract_text_from_html(full_link, soup=soup)
                source_type = "html"
                raw_text = html_text or ""
                logger.warning(f"  [PDF识别失败，回退HTML] {raw_title} ({len(raw_text)} 字符)")
        else:
            html_text = extract_text_from_html(full_link, soup=soup)
            if html_text and len(html_text) > 300:
                source_type = "html"
                raw_text = html_text
                if json_path.exists():
                    with open(json_path, encoding="utf-8") as f:
                        old = json.load(f)
                    if old.get("source_type") == "html" and old.get("ocr_success"):
                        logger.info(f"  [确认] 纯HTML正文，无需重抓: {case_id}")
                        return CaseData(**old)
                logger.info(f"  [HTML全文] {raw_title} ({len(raw_text)} 字符)")
            else:
                source_type = "html"
                raw_text = html_text or ""
                logger.warning(f"  [无内容] {raw_title} - HTML无正文且未找到PDF链接")

    punished_entity = extract_punished_entity(raw_title, raw_text or "", category_key)
    org_type = resolve_org_type(
        raw_title, raw_text or "", category_key, org_type_cache, punished_entity
    )

    case = CaseData(
        case_id=case_id,
        source_url=full_link,
        source_type=source_type,
        category="scfjg" if category_key == "Institution" else "scfry",
        title=raw_title,
        date=item_date.isoformat(),
        raw_text=raw_text or "",
        fetch_time=datetime.now().isoformat(),
        ocr_success=bool(raw_text and len(raw_text) > 50),
        error="" if raw_text else "未能提取到有效文本",
        pdf_url=pdf_url,
        org_type=org_type,
        punished_entity=punished_entity,
    )

    write_json(json_path, asdict(case))

    status = "✓" if case.ocr_success else "✗"
    logger.info(f"  [{status}] {case_id} | {source_type} | {len(case.raw_text)} 字符")

    return case


# ──────────────────────────── 列表页爬取 ────────────────────────────


def collect_case_links(
    category_key: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """从列表页收集案例链接，返回 [{link_url, title, date}, ...]"""
    config = CATEGORIES[category_key]
    base_url = config["url"]
    session = SessionManager()
    results = []
    page_index = 0
    should_stop = False
    consecutive_failures = 0

    while not should_stop:
        url = base_url + ("index.html" if page_index == 0 else f"index_{page_index}.html")
        logger.info(f"解析列表页 {page_index + 1}: {url}")

        page_ok = False
        for retry in range(LIST_PAGE_RETRIES):
            try:
                resp = session.get(url)
                if resp.status_code == 404:
                    logger.info("列表页404，翻页结束")
                    should_stop = True
                    page_ok = True
                    break
                resp.raise_for_status()
                if resp.encoding == "ISO-8859-1":
                    resp.encoding = resp.apparent_encoding

                soup = BeautifulSoup(resp.text, "html.parser")

                selectors = [
                    "ul.news_list li",
                    ".list-main li",
                    ".yl-list-con li",
                    "ul.txt-list li",
                    ".list ul li",
                    ".content ul li",
                ]
                items: list[Tag] = []
                for sel in selectors:
                    found = soup.select(sel)
                    if found:
                        items = found
                        break

                if not items:
                    main_area = as_tag(soup.find("div", class_=re.compile(r"right|main")))
                    if main_area:
                        items = [
                            li
                            for li in main_area.find_all("li")
                            if len(li.get_text(strip=True)) > 10
                        ]

                if not items:
                    logger.warning("列表页无列表结构，停止翻页")
                    should_stop = True
                    page_ok = True
                    break

                page_count = 0
                for item in items:
                    link_tag = as_tag(item.find("a", href=True))
                    if link_tag is None:
                        continue

                    full_row_text = item.get_text(strip=True)
                    raw_title = link_tag.get_text(strip=True)
                    link_url = attr_str(link_tag, "href")

                    item_date = parse_date_from_text(full_row_text)
                    if not item_date:
                        continue

                    if item_date > end_date:
                        continue
                    if item_date < start_date:
                        logger.info(f"遇到过期日期 {item_date}，停止翻页")
                        should_stop = True
                        break

                    results.append(
                        {
                            "link_url": link_url,
                            "title": raw_title,
                            "date": item_date,
                        }
                    )
                    page_count += 1

                logger.info(f"  第 {page_index + 1} 页收集到 {page_count} 个案例")
                page_ok = True
                break

            except Exception as e:
                if retry < LIST_PAGE_RETRIES - 1:
                    wait = 3 * (retry + 1)
                    logger.warning(
                        f"列表页请求失败 (重试 {retry + 1}/{LIST_PAGE_RETRIES}): {e}，{wait}秒后重试..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"列表页处理出错 (已重试{LIST_PAGE_RETRIES}次): {e}")

        if should_stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(
                f"第 {page_index + 1} 页跳过 (连续失败 {consecutive_failures}/{MAX_CONSECUTIVE_PAGE_FAILURES})"
            )
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                logger.error(f"连续 {MAX_CONSECUTIVE_PAGE_FAILURES} 页失败，停止翻页")
                break
            page_index += 1
            time.sleep(DELAY_BETWEEN_PAGES)
            continue

        consecutive_failures = 0
        page_index += 1
        time.sleep(DELAY_BETWEEN_PAGES)

    return results


# ──────────────────────────── 批量处理 ────────────────────────────


def fetch_category(
    category_key: str,
    start_date: date,
    end_date: date,
    output_dir: Path,
    index: CaseIndex,
    org_type_cache: OrgTypeCache | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, int]:
    """抓取一个分类下的所有案例"""
    config = CATEGORIES[category_key]
    logger.info(f"{'=' * 20} {config['name']} {'=' * 20}")

    output_dir.mkdir(parents=True, exist_ok=True)

    stats = index.get_stats(category_key)
    logger.info(
        f"索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
        f"待处理 {stats['pending']}, 失败 {stats['failed']}"
    )

    logger.info("从官网爬取案例列表 (列表页成本低，每次都刷新以确保完整性)...")
    case_links = collect_case_links(category_key, start_date, end_date)
    new_count = 0
    existing_urls = {item["link_url"] for item in index.get_category_links(category_key)}
    for link in case_links:
        if link["link_url"] not in existing_urls:
            new_count += 1
    index.update_category(category_key, case_links, last_page=0)
    logger.info(f"列表页收集到 {len(case_links)} 个案例，其中 {new_count} 个为新发现")

    pending = index.get_pending_links(category_key)
    failed_links = [
        item for item in index.get_category_links(category_key) if item.get("status") == "failed"
    ]
    to_process = pending + failed_links

    if not to_process:
        logger.info(f"{config['name']}: 无待处理案例")
        return stats["done"], 0

    logger.info(
        f"待处理: {len(pending)} pending + {len(failed_links)} failed = {len(to_process)} 个"
    )

    success = 0
    fail = 0

    for i, link_info in enumerate(to_process, 1):
        link_url = link_info["link_url"]
        raw_title = link_info["title"]
        item_date_str = link_info["date"]
        try:
            item_date = datetime.strptime(item_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            item_date = datetime.now().date()

        logger.info(f"[{i}/{len(to_process)}] {raw_title}")
        if on_progress:
            on_progress(i, len(to_process), raw_title)

        try:
            case = process_case(
                link_url=link_url,
                raw_title=raw_title,
                item_date=item_date,
                category_key=category_key,
                output_dir=output_dir,
                org_type_cache=org_type_cache,
            )

            if case is None:
                fail += 1
                index.mark_failed(category_key, link_url, "process_case返回None")
            elif case.ocr_success:
                success += 1
                index.mark_done(category_key, link_url)
            else:
                fail += 1
                index.mark_failed(category_key, link_url, case.error or "未能提取到有效文本")

        except Exception as e:
            logger.error(f"  处理失败: {e}")
            traceback.print_exc()
            fail += 1
            index.mark_failed(category_key, link_url, str(e))

        if i < len(to_process):
            time.sleep(DELAY_BETWEEN_API_CALLS)

    final_stats = index.get_stats(category_key)
    logger.info(f"{config['name']}: 本次成功 {success}, 失败 {fail}")
    logger.info(f"  索引总计: {final_stats['done']} done / {final_stats['total']} total")
    return success, fail


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
    config: Config | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """抓取指定日期范围内的 AMAC 纪律处分案例。

    Args:
        start_date: 起始日期（含）；与 ``end_date`` 任一为空时回落到上一季度。
        end_date: 结束日期（含）。
        categories: 抓取分类列表，可选 ``Institution`` / ``Personnel``；默认全部。
        config: 配置对象，默认使用全局单例。
        on_progress: 每条案例处理前的回调 ``(序号, 总数, 标题)``，供界面展示进度。

    Returns:
        :class:`FetchResult` 汇总结果。
    """
    cfg = config or get_config()
    if start_date is None or end_date is None:
        default_start, default_end = get_last_quarter_range()
        start_date = start_date or default_start
        end_date = end_date or default_end

    selected = [item for item in (categories or list(CATEGORIES)) if item in CATEGORIES]
    if not selected:
        selected = list(CATEGORIES)

    cases_root = cfg.data_root("amac_cases")
    cases_root.mkdir(parents=True, exist_ok=True)
    index = AmacIndex(cases_root)
    org_type_cache = OrgTypeCache(cases_root)

    result = FetchResult()
    started = time.time()
    logger.info("=" * 20 + " AMAC 案例抓取 " + "=" * 20)
    logger.info("日期范围: %s ~ %s | 分类: %s", start_date, end_date, "、".join(selected))
    logger.info("输出目录: %s", cases_root)

    try:
        for category_key in selected:
            category_dir = cases_root / category_key.lower()
            success, failed = fetch_category(
                category_key=category_key,
                start_date=start_date,
                end_date=end_date,
                output_dir=category_dir,
                index=index,
                org_type_cache=org_type_cache,
                on_progress=on_progress,
            )
            result.success += success
            result.failed += failed
            result.detail[category_key] = index.get_stats(category_key)
    finally:
        SessionManager().close()

    logger.info(
        "AMAC 抓取完成：成功 %d，失败 %d，耗时 %.1f 秒",
        result.success,
        result.failed,
        time.time() - started,
    )
    return result


def fetch_pending(
    categories: list[str] | None = None,
    config: Config | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> FetchResult:
    """断点续传：处理索引中全部 ``pending`` 与 ``failed`` 的案例。"""
    logger.info("断点续传模式：处理索引中未完成与失败的案例")
    return fetch(
        start_date=datetime.min.date(),
        end_date=datetime.max.date(),
        categories=categories,
        config=config,
        on_progress=on_progress,
    )


def fetch_single(
    url: str,
    category: str = "Institution",
    config: Config | None = None,
) -> CaseData | None:
    """抓取单个案例 URL 并落库。"""
    cfg = config or get_config()
    cases_root = cfg.data_root("amac_cases")
    cases_root.mkdir(parents=True, exist_ok=True)
    org_type_cache = OrgTypeCache(cases_root)
    try:
        return process_case(
            link_url=url,
            raw_title="手动输入案例",
            item_date=datetime.now().date(),
            category_key=category,
            output_dir=cases_root,
            org_type_cache=org_type_cache,
        )
    finally:
        SessionManager().close()


if __name__ == "__main__":  # pragma: no cover - 便于单文件调试
    configure_logging()
    fetch()
