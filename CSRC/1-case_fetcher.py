"""证监会基金相关行政处罚与监管措施数据库 - 主抓取脚本。

本脚本从中国证监会官网（www.csrc.gov.cn）抓取会本部及 36 家派出机构的
行政处罚决定与行政监管措施案例，自动识别基金相关条目，提取 HTML
正文全文，落库为统一格式的 JSON 文件。

主要能力：
    - 多来源（37 个证监局）× 多类型（行政处罚 / 监管措施）的列表页爬取
    - 基金相关性过滤（标题预过滤 + 内容确认）
    - 详情页 HTML 正文提取（修复行内标签导致的分段问题）
    - 案例索引，支持增量抓取与断点续传
    - CLI 交互入口

架构参考 AMAC 的 1-case_fetcher.py，但适配证监会体系的多来源、多类型、
基金过滤需求。本模块自包含，不依赖 AMAC 代码，仅依赖同目录下的 bureaus.py。
"""

from __future__ import annotations

import os
import re
import json
import time
import logging
import sys
import traceback
import threading
import types as _types
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlencode
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass, asdict
from urllib.parse import parse_qs
from copy import copy

# 兼容 Python 3.12：通过 importlib.util.exec_module 加载本文件时，模块对象
# 尚未注册到 sys.modules，而 @dataclass 装饰器内部会调用
# sys.modules.get(cls.__module__).__dict__，未注册时会抛出 AttributeError。
# 这里预先注册一个占位模块对象，确保 dataclass 装饰能正常工作。
# 正常 `import` 场景下 __name__ 已在 sys.modules 中，不会触发此分支。
if __name__ not in sys.modules:
    _placeholder = _types.ModuleType(__name__)
    _placeholder.__file__ = __file__ if "__file__" in dir() else ""
    sys.modules[__name__] = _placeholder

import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup

# 复用同目录 bureaus.py 提供的证监局配置
from bureaus import BUREAUS, Bureau, discover_penalty_url


# ──────────────────────────── 全局配置 ────────────────────────────

# 案例类型常量
CASE_TYPE_PENALTY = "penalty"   # 行政处罚
CASE_TYPE_MEASURE = "measure"   # 监管措施

# 默认抓取起始日期（spec 要求覆盖 2022-01-01 至今）
DEFAULT_START_DATE = datetime(2022, 1, 1).date()

# HTTP 请求相关常量
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TIMEOUT = 30
MAX_RETRIES = 3
DELAY_BETWEEN_PAGES = 1.5         # 列表页翻页间隔
DELAY_BETWEEN_CASES = 1.0         # 串行模式下详情页请求之间的间隔（并发模式下不使用）
LIST_PAGE_RETRIES = 3             # 单个列表页请求失败重试次数
MAX_CONSECUTIVE_PAGE_FAILURES = 3 # 连续多页失败上限

# 并发抓取相关配置
DEFAULT_CONCURRENCY = 8           # 默认线程池大小（单源内并发）
MAX_CONCURRENCY = 32              # 允许的最大并发数
MIN_CONCURRENCY = 1               # 最小并发数（1 即串行模式）

# 案例类型中文映射
CASE_TYPE_CN = {
    CASE_TYPE_PENALTY: "行政处罚",
    CASE_TYPE_MEASURE: "监管措施",
}


# ──────────────────────────── 日志 ────────────────────────────

# 常规进度信息采用行内刷新（\r 覆盖前一行），避免终端被翻页、收集计数、
# 单案例处理等大量常规日志刷屏。仅异常（WARNING/ERROR）与最终结果信息
# 走标准 logger 输出，自动换行到新行。
_progress_lock = threading.Lock()
_last_progress_width = 0  # 上一次 progress 输出的可见宽度，用于填充清空残留


def _visible_width(s: str) -> int:
    """估算字符串在终端的可见宽度（非 ASCII 字符按 2 列计）。"""
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


def progress(msg: str) -> None:
    """行内刷新输出常规进度信息（覆盖前一行，不换行）。

    用于翻页、收集计数、单案例处理等常规进度。后续任意 logger 输出会
    自动换行到新行，保证最终结果与异常信息可读。
    """
    global _last_progress_width
    with _progress_lock:
        sys.stdout.write("\r" + msg)
        # 用空格填充到上一次宽度，避免行尾残留字符
        pad = max(0, _last_progress_width - _visible_width(msg))
        if pad:
            sys.stdout.write(" " * pad)
        sys.stdout.flush()
        _last_progress_width = _visible_width(msg)


class _ProgressAwareStreamHandler(logging.StreamHandler):
    """输出日志前，若有未完成的 progress 行，先换行。"""

    def emit(self, record):
        global _last_progress_width
        with _progress_lock:
            if _last_progress_width > 0:
                sys.stdout.write("\n")
                _last_progress_width = 0
        super().emit(record)


def setup_logging() -> logging.Logger:
    """配置并返回模块日志器。"""
    log = logging.getLogger("csrc_case_fetcher")
    log.setLevel(logging.INFO)
    if log.handlers:
        return log
    handler = _ProgressAwareStreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    log.addHandler(handler)
    return log


logger = setup_logging()


# ──────────────────────────── 数据模型 ────────────────────────────

@dataclass
class CaseData:
    """单个案例的完整数据模型。

    字段对齐 CSRC 详情页实际内容：元数据表格包含索引号、分类、发布机构、
    发文日期、名称、文号、主题词。
    """
    case_id: str               # 唯一标识，如 20260213_c7615688
    source_url: str            # 详情页 URL
    case_type: str             # penalty | measure
    bureau: str                # 来源局英文标识，如 HQ / Beijing
    title: str                 # 案例标题
    date: str                  # 发布日期 YYYY-MM-DD
    raw_text: str = ""         # 清洗后的正文全文
    fetch_time: str = ""
    error: str = ""
    is_fund_related: bool = False   # 是否基金相关
    fund_evidence: str = ""         # 基金判定依据
    document_number: str = ""       # 文号，如"〔2025〕47号"，从正文开头提取
    punished_entities: str = ""     # 受处罚主体（从标题或正文提取，多个用顿号分隔）
    pdf_url: str = ""              # PDF 附件 URL（如有，不做 OCR）
    doc_url: str = ""              # Word 文档附件 URL（如有，已提取文本）


# ──────────────────────────── HTTP Session ────────────────────────────

class SessionManager:
    """全局 HTTP 会话单例，带重试与连接池。"""

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
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_CONCURRENCY + 4,
                              pool_maxsize=MAX_CONCURRENCY + 4)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get(self, url, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return self.session.get(url, **kwargs)

    def post(self, url, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        return self.session.post(url, **kwargs)

    def close(self):
        if self.session:
            self.session.close()
            SessionManager._instance = None


# ──────────────────────────── 工具函数 ────────────────────────────

def extract_id_from_url(url: str) -> str:
    """从详情页 URL 提取案例唯一标识。

    CSRC 详情页 URL 形如：
        https://www.csrc.gov.cn/csrc/c106068/c7615688/content.shtml
    其中 c7615688 即为内容记录的唯一编号，用作 case_id 主体。
    """
    # 优先匹配 cXXXXXXX/content.shtml 模式
    m = re.search(r"/(c\d+)/content\.shtml", url)
    if m:
        return m.group(1)
    # 退化：匹配任意 c+数字 段
    m = re.search(r"(c\d{6,})", url)
    if m:
        return m.group(1)
    # 最终退化：取 URL 最后一段
    return re.sub(r"[^a-zA-Z0-9]", "_", url.split("/")[-1])[:60]


def build_case_id(date_str: str, url: str) -> str:
    """生成案例 ID：日期前缀 + URL 内容编号，如 20260213_c7615688。"""
    content_id = extract_id_from_url(url)
    # 规范日期为 YYYYMMDD
    date_compact = re.sub(r"\D", "", date_str or "")[:8]
    if date_compact:
        return f"{date_compact}_{content_id}"
    return content_id


def parse_date_from_text(text: str) -> Optional[datetime.date]:
    """从文本中解析日期，支持 YYYY-MM-DD 与 YYYY年MM月DD日。"""
    if not text:
        return None
    m = re.search(r"(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    return None


def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符。"""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


# ──────────────────────────── 案例索引 ────────────────────────────

class CaseIndex:
    """持久化的案例索引，按 {bureau}/{case_type} 维度组织。

    索引文件：{output_dir}/_index.json
    结构：
    {
      "last_updated": "...",
      "sources": {
        "HQ/penalty": {"last_crawled_page": 0, "links": [...]},
        "HQ/measure": {"last_crawled_page": 0, "links": [...]},
        "Beijing/measure": {"last_crawled_page": 0, "links": [...]},
        ...
      }
    }
    每条 link 含：link_url、title、date、status（pending/done/failed/skipped_not_fund）。
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.index_path = output_dir / "_index.json"
        self.data: Dict[str, Any] = self._load()
        # 读写锁：并发抓取时保证 mark_done/mark_failed/mark_skipped 等操作的原子性
        # 使用 RLock 因为 mark_* 内部会调用 save()，save 也持锁
        self._lock = threading.RLock()

    @staticmethod
    def source_key(bureau_name_en: str, case_type: str) -> str:
        """构造索引中的来源键，如 'HQ/penalty'。"""
        return f"{bureau_name_en}/{case_type}"

    def _load(self) -> Dict[str, Any]:
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"索引文件损坏，将重建: {e}")
        return {"last_updated": "", "sources": {}}

    def save(self) -> None:
        """持久化索引文件。调用方应已持有 _lock。"""
        self.data["last_updated"] = datetime.now().isoformat()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.index_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        # 原子替换，避免并发写入产生半截文件
        os.replace(tmp_path, self.index_path)

    def get_source_links(self, source_key: str) -> List[Dict]:
        with self._lock:
            src = self.data["sources"].get(source_key, {})
            # 返回浅拷贝列表，避免调用方在锁外修改时影响内部状态
            return [dict(item) for item in src.get("links", [])]

    def get_last_crawled_page(self, source_key: str) -> int:
        with self._lock:
            src = self.data["sources"].get(source_key, {})
            return src.get("last_crawled_page", 0)

    def update_source(self, source_key: str, links: List[Dict], last_page: int) -> None:
        """合并新发现的链接到指定来源；已存在的 URL 仅刷新 title/date，不重置状态。"""
        with self._lock:
            existing = self.data["sources"].get(source_key, {})
            existing_links: Dict[str, Dict] = {
                item["link_url"]: item for item in existing.get("links", [])
            }

            for link in links:
                url = link["link_url"]
                date_val = link["date"]
                date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
                if url in existing_links:
                    existing_links[url]["date"] = date_str
                    existing_links[url]["title"] = link["title"]
                else:
                    existing_links[url] = {
                        "link_url": url,
                        "title": link["title"],
                        "date": date_str,
                        "status": "pending",
                    }

            existing["links"] = list(existing_links.values())
            existing["last_crawled_page"] = last_page
            self.data["sources"][source_key] = existing
            self.save()

    def _find_link(self, source_key: str, link_url: str) -> Optional[Dict]:
        """查找指定链接。调用方应已持有 _lock。"""
        src = self.data["sources"].get(source_key, {})
        for item in src.get("links", []):
            if item["link_url"] == link_url:
                return item
        return None

    def mark_done(self, source_key: str, link_url: str) -> None:
        with self._lock:
            item = self._find_link(source_key, link_url)
            if item:
                item["status"] = "done"
                self.save()

    def mark_failed(self, source_key: str, link_url: str, error: str = "") -> None:
        with self._lock:
            item = self._find_link(source_key, link_url)
            if item:
                item["status"] = "failed"
                if error:
                    item["error"] = error
                self.save()

    def mark_skipped(self, source_key: str, link_url: str, reason: str = "not_fund") -> None:
        with self._lock:
            item = self._find_link(source_key, link_url)
            if item:
                item["status"] = "skipped_not_fund"
                item["skip_reason"] = reason
                self.save()

    def get_pending_links(self, source_key: str) -> List[Dict]:
        with self._lock:
            return [dict(item) for item in self.get_source_links(source_key)
                    if item.get("status") == "pending"]

    def get_stats(self, source_key: str) -> Dict[str, int]:
        with self._lock:
            src = self.data["sources"].get(source_key, {})
            links = src.get("links", [])
            stats = {"total": len(links), "done": 0, "pending": 0, "failed": 0, "skipped_not_fund": 0}
            for item in links:
                s = item.get("status", "pending")
                if s in stats:
                    stats[s] += 1
            return stats


# ──────────────────────────── 受处罚主体提取 ────────────────────────────

def extract_org_name_from_title(title: str) -> Optional[str]:
    """从案例标题中正则提取受处罚机构名称。

    证监会标题常见形式：
        关于对XX投资管理有限公司采取警示函措施的决定
        关于对XX证券股份有限公司的行政处罚决定
    """
    patterns = [
        r"关于对[《]?([^》]+?)[》]?(?:的)?(?:行政处罚|监管措施|采取|撤销|注销|暂停|取消|责令|警示|监管谈话)",
        r"关于对[《]?([^》]+?)[》]?的",
        r"关于[《]?([^》]+?)[》]?(?:的)?(?:行政处罚|监管措施|决定)",
    ]
    for pattern in patterns:
        m = re.search(pattern, title)
        if m:
            name = m.group(1).strip()
            for suffix in ["的行政处罚决定书", "行政处罚决定书", "的监管措施决定书", "监管措施决定书",
                           "的决定书", "决定书", "送达公告", "（送达公告）", "(送达公告)",
                           "事先告知书", "复核决定书"]:
                name = name.replace(suffix, "")
            name = name.rstrip("的、，,")
            if len(name) >= 4:
                return name

    # 括号内含机构名
    m = re.search(r"[（(]((?:[^()（）]|[（(][^)）]*[)）])+)[)）]", title)
    if m:
        inner = m.group(1).strip()
        parts = re.split(r"[、，,]", inner)
        for part in parts:
            part = part.strip()
            if re.search(r"(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本|投资)", part):
                if len(part) >= 4:
                    return part

    # 标题中含机构后缀的连续串
    m = re.search(r"([^\s,，、（）\(\)]+(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本))", title)
    if m:
        name = m.group(1).strip()
        if len(name) >= 4:
            return name
    return None


def extract_full_org_name_from_text(short_name: Optional[str], raw_text: str) -> Optional[str]:
    """从正文正则提取机构完整名称（当标题只有简称时使用）。"""
    if not raw_text or len(raw_text) < 10:
        return None

    snippet = raw_text[:3000]

    if short_name:
        escaped = re.escape(short_name)
        m = re.search(r"([^，,：:；;\n]+)（以下简称" + escaped + r"）", snippet)
        if m:
            name = m.group(1).strip()
            for prefix in ["当事人：", "当事人:", "被申请人：", "被申请人:",
                           "申请人：", "申请人:", "被处罚人：", "被处罚人:",
                           "被处置机构：", "被处置机构:", "被调查人：", "被调查人:"]:
                if name.startswith(prefix):
                    name = name[len(prefix):].strip()
            if len(name) >= 4 and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", name):
                return name

    for prefix in ["当事人", "被申请人", "申请人", "被处罚人", "被处置机构", "被调查人"]:
        m = re.search(
            prefix + r"[：:]\s*([^\n,，。；;（(]+?(?:公司|企业|有限|合伙|事务所|集团)[^\n]*?)(?:[，,。\n（(]|$)",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            if len(name) >= 4:
                return name

    for prefix in ["当事人", "被申请人", "申请人", "被处罚人", "被处置机构", "被调查人"]:
        m = re.search(
            prefix + r"[：:]\s*([^\n，,。；;]+?(?:有限公司|股份有限公司|企业|合伙|事务所|集团))",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            if len(name) >= 4:
                return name

    m = re.search(
        r"([^，,：:；;\n]+?(?:公司|企业|有限|合伙|事务所|集团)[^，,：:；;\n]*?)（以下简称",
        snippet,
    )
    if m:
        name = m.group(1).strip()
        for prefix in ["当事人：", "当事人:", "被申请人：", "被申请人:",
                       "申请人：", "申请人:", "被处罚人：", "被处罚人:"]:
            if name.startswith(prefix):
                name = name[len(prefix):].strip()
        if len(name) >= 4:
            return name

    return None


def extract_punished_entities(title: str, raw_text: str) -> str:
    """提取受处罚主体名称，返回字符串（多个用顿号分隔）。

    优先从标题正则提取；若标题提取结果不含机构后缀，则尝试从正文提取完整名称。
    """
    name = extract_org_name_from_title(title)
    if name and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", name):
        return name
    if raw_text:
        full_name = extract_full_org_name_from_text(name, raw_text)
        if full_name:
            return full_name
    return name or ""


# ──────────────────────────── 文号提取 ────────────────────────────

def extract_document_number(raw_text: str) -> str:
    """从正文开头提取文号，如'〔2025〕47号'、'沪〔2023〕31号'。"""
    if not raw_text:
        return ""
    snippet = raw_text[:500]
    # 匹配模式：可选前缀 + 〔年份〕序号号
    patterns = [
        r"([\u4e00-\u9fa5]{0,4})[〔［\[](\d{4})[〕］\]](\d+)号",
        r"(\d{4})年(\d+)号",
    ]
    for pattern in patterns:
        m = re.search(pattern, snippet)
        if m:
            return m.group(0)
    return ""


# ──────────────────────────── 基金相关性过滤 ────────────────────────────

# 标题中明确属于非基金领域的关键词
NON_FUND_TITLE_KEYWORDS = [
    "证券公司", "证券股份有限公司", "证券有限责任公司", "证券有限公司", "证券承销保荐",
    "期货公司", "期货有限公司", "期货股份有限公司",
    "上市公司", "股份有限公司（上市公司）",
    "银行", "商业银行", "股份制银行",
    "保险", "保险公司", "保险集团",
    "信托公司",
    "财务公司",
    "金融控股",
    "消费金融公司",
    "汽车金融公司",
    "证券投资咨询公司",
]


def is_fund_by_title(title: str) -> Optional[bool]:
    """根据标题预判是否基金相关。

    Returns:
        True  —— 标题含"基金"
        False —— 标题明确属于非基金领域
        None  —— 标题模糊，需内容确认
    """
    if not title:
        return None
    if "基金" in title:
        return True
    for kw in NON_FUND_TITLE_KEYWORDS:
        if kw in title:
            return False
    return None


def is_fund_by_content(raw_text: str) -> bool:
    """根据正文确认是否基金相关：检查是否含"基金"。"""
    if not raw_text:
        return False
    return "基金" in raw_text


# ──────────────────────────── HTML 页面获取 ────────────────────────────

def fetch_html_page(html_url: str) -> Optional[BeautifulSoup]:
    """获取 HTML 页面并返回 BeautifulSoup 对象。

    CSRC 网站使用 UTF-8，但 requests 在 Content-Type 未声明 charset 时
    会误判为 ISO-8859-1，这里统一回退到 apparent_encoding。
    """
    try:
        session = SessionManager()
        resp = session.get(html_url)
        resp.raise_for_status()
        if resp.encoding == "ISO-8859-1" or resp.encoding is None:
            resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.warning(f"HTML页面获取失败 {html_url}: {e}")
        return None


# ──────────────────────────── HTML 正文提取 ────────────────────────────

def extract_text_from_html(html_url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
    """从 CSRC 详情页 HTML 提取正文文本，修复行内标签导致的分段问题。

    CSRC 详情页正文容器为 div.detail-news，内部用大量 <span><font> 包裹
    文字片段。直接用 get_text(separator="\\n") 会在每个 span 边界插入换行，
    导致句子碎片化。本函数先 unwrap 行内标签，再按块级元素分段。
    """
    try:
        if soup is None:
            soup = fetch_html_page(html_url)
        if soup is None:
            return None

        # 查找正文容器
        content_area = (
            soup.find("div", class_="detail-news")
            or soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="Custom_UnionStyle")
            or soup.find("div", class_=re.compile(r"detail|article|content|text", re.I))
            or soup.find("div", id=re.compile(r"detail|article|content|zoom|zoom_content", re.I))
        )
        if not content_area:
            main_div = soup.find("div", class_=re.compile(r"right|main|cont"))
            if main_div:
                content_area = main_div

        if not content_area:
            body_text = soup.get_text(separator="\n", strip=True)
            body_text = re.sub(r"\n{3,}", "\n\n", body_text)
            return body_text if len(body_text) > 200 else None

        # 移除无关标签
        for tag in content_area.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        # 移除打印/关闭等按钮区
        for tag in content_area.find_all("div", class_=re.compile(r"xxgk-down|down-box|print|clear")):
            tag.decompose()

        # 复制一份避免修改原 soup
        work_area = copy(content_area)

        # 1) 将 <br> 标签替换为换行符标记
        for br in work_area.find_all("br"):
            br.replace_with("\n")

        # 2) unwrap 行内标签（span, font, a, b, i, em, strong, u, sub, sup, o:p, center）
        inline_tags = ["span", "font", "a", "b", "i", "em", "strong", "u",
                       "sub", "sup", "o:p", "center", "mark", "small", "big"]
        for tag_name in inline_tags:
            for tag in work_area.find_all(tag_name):
                tag.unwrap()

        # 3) 在块级标签后添加换行符
        block_tags = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
                      "tr", "table", "blockquote", "pre"]
        for tag_name in block_tags:
            for tag in work_area.find_all(tag_name):
                tag.append("\n")

        # 4) 提取文本（不再用 \n 作为 separator，因为已手动添加换行）
        text = work_area.get_text(separator="", strip=False)

        # 5) 清理多余空白
        # 合并被换行符分隔的空白行
        lines = text.split("\n")
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if line:
                cleaned_lines.append(line)

        text = "\n".join(cleaned_lines)
        text = re.sub(r"\n{3,}", "\n\n", text)

        if len(text) > 50:
            return text
        return None
    except Exception as e:
        logger.warning(f"HTML正文提取失败 {html_url}: {e}")
        return None


# ──────────────────────────── PDF 链接发现 ────────────────────────────

def find_pdf_link_in_page(page_url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
    """从 HTML 详情页查找内嵌 PDF 链接，优先附件下载区。"""
    try:
        if soup is None:
            soup = fetch_html_page(page_url)
        if soup is None:
            return None

        # 1) 附件下载区
        for container in soup.find_all("div", class_="attachment-down"):
            for link in container.find_all("a", href=True):
                href = link["href"]
                if ".pdf" in href.lower():
                    pdf_url = urljoin(page_url, href)
                    progress(f"  [附件区PDF] {pdf_url}")
                    return pdf_url

        # 2) 含"附件"文字的容器
        for heading in soup.find_all(string=re.compile(r"附件.*下载|下载.*附件")):
            parent_div = heading.find_parent("div")
            if parent_div:
                for link in parent_div.find_all("a", href=True):
                    href = link["href"]
                    if ".pdf" in href.lower():
                        pdf_url = urljoin(page_url, href)
                        progress(f"  [附件区PDF] {pdf_url}")
                        return pdf_url

        # 3) class 含 fujian/attachment/download 的容器
        for container in soup.find_all("div", class_=re.compile(r"fujian|attachment|download|accessory", re.I)):
            for link in container.find_all("a", href=True):
                href = link["href"]
                if ".pdf" in href.lower():
                    pdf_url = urljoin(page_url, href)
                    progress(f"  [下载区PDF] {pdf_url}")
                    return pdf_url

        # 4) 全页面查找，过滤侧边栏链接
        sidebar_prefixes = ("hydj", "xwfb", "hdjl", "hyyj", "sjtj", "zwgk", "tz")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if ".pdf" not in href.lower():
                continue
            parts = href.replace("\\", "/").split("/")
            if any(p in parts for p in sidebar_prefixes):
                continue
            pdf_url = urljoin(page_url, href)
            progress(f"  [页面PDF] {pdf_url}")
            return pdf_url

        # 5) iframe / embed
        iframe = soup.find("iframe", src=True)
        if iframe and ".pdf" in iframe["src"].lower():
            return urljoin(page_url, iframe["src"])

        for embed in soup.find_all("embed", src=True):
            if ".pdf" in embed["src"].lower():
                return urljoin(page_url, embed["src"])

        return None
    except Exception as e:
        logger.warning(f"PDF链接查找失败 {page_url}: {e}")
        return None


def find_doc_link_in_page(page_url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
    """从 HTML 详情页查找内嵌 Word 文档（.doc/.docx）链接。

    内蒙古等局的部分行政处罚决定书以 Word 附件形式提供，详情页正文容器
    内只有一个 .docx 下载链接。本函数检测此类链接并返回绝对 URL。

    Args:
        page_url: 详情页 URL，用于解析相对链接。
        soup: 已解析的 BeautifulSoup 对象；为 None 时自动获取。

    Returns:
        Word 文档的绝对 URL；未找到返回 None。
    """
    try:
        if soup is None:
            soup = fetch_html_page(page_url)
        if soup is None:
            return None

        # 优先在正文容器内查找
        content_area = (
            soup.find("div", class_="detail-news")
            or soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_=re.compile(r"detail|article|content", re.I))
        )
        search_scope = content_area or soup

        # 查找 .doc/.docx 链接
        for link in search_scope.find_all("a", href=True):
            href = link["href"]
            if re.search(r"\.(?:docx?|wps)(?:$|\?)", href, re.I):
                doc_url = urljoin(page_url, href)
                progress(f"  [Word文档] {doc_url}")
                return doc_url

        return None
    except Exception as e:
        logger.warning(f"Word文档链接查找失败 {page_url}: {e}")
        return None


def _detect_doc_format(content: bytes) -> str:
    """根据 magic bytes 检测文档实际格式。

    CSRC 附件常见三种情况：
    - OOXML (.docx)：PK 开头的 ZIP
    - OLE 复合文档 (.doc 或 .docx 误命名)：D0CF11E0 开头
    - PDF：%PDF 开头

    Args:
        content: 文件字节内容。

    Returns:
        'ole' | 'ooxml' | 'pdf' | 'unknown'
    """
    if content[:8] == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        return "ole"
    if content[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "ooxml"
    if content[:5] == b"%PDF-":
        return "pdf"
    return "unknown"


def _extract_text_from_ole(content: bytes) -> str:
    """从 OLE 复合文档（.doc 旧格式）提取正文文本。

    通过 olefile 读取 WordDocument 流与 Table 流，解析 piece table
    （CLX 中的 PLCFpcd）按 CP 顺序拼接文本片段。Word Binary Format
    中文本可能为 Unicode（UTF-16-LE）或 ANSI（cp936）。

    Args:
        content: .doc 文件字节内容。

    Returns:
        提取的纯文本；失败返回空字符串。
    """
    import io
    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(content))
    except Exception as e:
        logger.warning(f"  [Word文档] OLE 打开失败: {e}")
        return ""

    try:
        if not ole.exists("WordDocument"):
            return ""
        word_stream = ole.openstream("WordDocument").read()

        # piece table 存放在 0Table 或 1Table 流
        table_name = "0Table" if ole.exists("0Table") else "1Table"
        if not ole.exists(table_name):
            return _fallback_extract_ole_text(word_stream)
        table_stream = ole.openstream(table_name).read()

        # CLX = Prc* + Pcdt；Pcdt 以 0x02 标志开头，后跟 4 字节 lcb
        # PLCFpcd = (n+1)*CP(4字节) + n*PCD(8字节)，故 lcb = 12n+4
        clx_offset = -1
        clx_len = 0
        idx = 0
        while idx < len(table_stream) - 6:
            if table_stream[idx] == 0x02:
                lcb = int.from_bytes(table_stream[idx + 1: idx + 5], "little")
                if 0 < lcb < len(table_stream) - idx - 5 and (lcb - 4) % 12 == 0:
                    clx_offset = idx + 5
                    clx_len = lcb
                    break
            idx += 1

        if clx_offset < 0:
            return _fallback_extract_ole_text(word_stream)

        plc = table_stream[clx_offset: clx_offset + clx_len]
        n = (clx_len - 4) // 12
        cps = [int.from_bytes(plc[i * 4: (i + 1) * 4], "little") for i in range(n + 1)]
        pcds_start = (n + 1) * 4

        text_parts: List[str] = []
        for i in range(n):
            pcd = plc[pcds_start + i * 8: pcds_start + (i + 1) * 8]
            fc_raw = int.from_bytes(pcd[2:6], "little")
            # bit30=1 表示 ANSI 压缩编码（fc 需除以 2），bit30=0 表示 Unicode
            is_unicode = not (fc_raw & 0x40000000)
            fc = fc_raw & 0x3FFFFFFF
            char_count = cps[i + 1] - cps[i]
            if is_unicode:
                raw = word_stream[fc: fc + char_count * 2]
                text_parts.append(raw.decode("utf-16-le", errors="ignore"))
            else:
                fc = fc // 2
                raw = word_stream[fc: fc + char_count]
                text_parts.append(raw.decode("cp936", errors="ignore"))
        return "".join(text_parts)
    finally:
        ole.close()


def _fallback_extract_ole_text(stream: bytes) -> str:
    """OLE piece table 解析失败时，从 WordDocument 流直接提取文本片段。

    将整个流按 UTF-16-LE 解码后，用正则匹配连续的中英文可打印片段。
    质量不如 piece table 解析，但能兜底。
    """
    text = stream.decode("utf-16-le", errors="ignore")
    chunks = re.findall(
        r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\w\s，。；：、（）《》“”‘’！？·…—\-.\d()\[\]/%]+",
        text,
    )
    return "".join(chunks)


def _extract_text_from_ooxml(content: bytes) -> str:
    """从 OOXML (.docx) 提取文本。

    优先使用 python-docx（同时提取段落与表格）；失败时回退到直接
    解析 ZIP 内的 word/document.xml。
    """
    import io

    try:
        import docx  # python-docx

        document = docx.Document(io.BytesIO(content))
        paragraphs: List[str] = []
        for para in document.paragraphs:
            text = para.text.strip()
            if text:
                paragraphs.append(text)
        # 也提取表格中的文本（行政处罚决定书可能用表格列当事人信息）
        for table in document.tables:
            for row in table.rows:
                row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_texts:
                    paragraphs.append(" | ".join(row_texts))
        return "\n".join(paragraphs)
    except Exception as e:
        logger.warning(f"  [Word文档] python-docx 失败，回退到 XML 解析: {e}")
        return _extract_text_from_ooxml_xml(content)


def _extract_text_from_ooxml_xml(content: bytes) -> str:
    """python-docx 失败时，直接从 ZIP 中解析 word/document.xml 提取文本。"""
    import io
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            doc_name = None
            for name in zf.namelist():
                if name.endswith("word/document.xml"):
                    doc_name = name
                    break
            if not doc_name:
                return ""
            with zf.open(doc_name) as f:
                tree = ET.parse(f)
        root = tree.getroot()
        w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs: List[str] = []
        for para in root.iter(f"{w_ns}p"):
            texts = [t.text for t in para.iter(f"{w_ns}t") if t.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except Exception as e:
        logger.warning(f"  [Word文档] XML 解析失败: {e}")
        return ""


def extract_text_from_docx(doc_url: str) -> Optional[str]:
    """下载 Word 文档并提取纯文本，自动检测实际格式。

    CSRC 地方局附件常见三类情况：
    1. 标准 .docx（OOXML/ZIP）→ python-docx
    2. .doc 旧格式（OLE 复合文档）→ olefile 解析 piece table
    3. 扩展名为 .docx 但实际是 .doc（OLE）→ 同 2

    通过 magic bytes 检测实际格式后分发，避免依赖扩展名。

    Args:
        doc_url: Word 文档的 URL。

    Returns:
        提取的纯文本；失败或文本过短返回 None。
    """
    try:
        session = SessionManager()
        resp = session.get(doc_url)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        logger.warning(f"  [Word文档] 下载失败 {doc_url}: {e}")
        return None

    fmt = _detect_doc_format(content)
    if fmt == "ole":
        text = _extract_text_from_ole(content)
    elif fmt == "ooxml":
        text = _extract_text_from_ooxml(content)
    elif fmt == "pdf":
        # 极少数 Word 链接实际指向 PDF
        progress("  [Word文档] 实际为 PDF，转用 PDF 提取")
        text = _extract_text_from_pdf_bytes(content)
    else:
        logger.warning(f"  [Word文档] 未知格式（magic={content[:8].hex()}），尝试 OLE 回退")
        text = _extract_text_from_ole(content)

    if not text or len(text) <= 50:
        logger.warning(f"  [Word文档] 提取文本过短（{len(text or '')} 字符，格式={fmt}）")
        return None

    text = re.sub(r"\n{3,}", "\n\n", text)
    progress(f"  [Word文档] 提取成功（格式={fmt}），{len(text)} 字符")
    return text


def _extract_text_from_pdf_bytes(content: bytes) -> str:
    """从 PDF 字节内容提取文本（非 OCR，仅文本型 PDF 有效）。"""
    import io

    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("  [PDF] pypdf 未安装，无法提取文本")
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception as e:
        logger.warning(f"  [PDF] 提取失败: {e}")
        return ""


def extract_text_from_pdf(pdf_url: str) -> Optional[str]:
    """下载 PDF 并提取文本（非 OCR，仅文本型 PDF 有效）。

    CSRC 部分行政处罚/监管措施以 PDF 附件形式提供，正文为可提取的
    文本型 PDF（非扫描件）。本函数用 pypdf 提取文本；若为扫描型 PDF
    则返回 None（不做 OCR）。

    Args:
        pdf_url: PDF 文件的 URL。

    Returns:
        提取的纯文本；失败或扫描型 PDF 返回 None。
    """
    try:
        session = SessionManager()
        resp = session.get(pdf_url)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        logger.warning(f"  [PDF] 下载失败 {pdf_url}: {e}")
        return None

    text = _extract_text_from_pdf_bytes(content)
    if not text or len(text) <= 50:
        logger.warning(f"  [PDF] 提取文本过短（{len(text or '')} 字符），可能是扫描型 PDF")
        return None

    text = re.sub(r"\n{3,}", "\n\n", text)
    progress(f"  [PDF] 提取成功，{len(text)} 字符")
    return text


# ──────────────────────────── 单案例处理 ────────────────────────────

def process_case(
    link_url: str,
    title: str,
    date_str: str,
    bureau_name: str,
    case_type: str,
    output_dir: Path,
    skip_fund_check: bool = False,
) -> Optional[CaseData]:
    """处理单个案例链接：提取内容 → 基金过滤 → 文号/主体提取 → 落盘。

    Args:
        link_url: 详情页 URL
        title: 案例标题
        date_str: 发布日期 YYYY-MM-DD
        bureau_name: 来源局英文标识
        case_type: penalty / measure
        output_dir: 该来源+类型的输出目录
        skip_fund_check: 跳过基金过滤（标题已确认基金时为 True）

    Returns:
        CaseData 对象；若被基金过滤跳过则返回 None。
    """
    case_id = build_case_id(date_str, link_url)
    json_path = output_dir / f"{case_id}.json"

    # 已存在且成功的记录直接复用
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing_case = CaseData(**existing)
            if existing_case.raw_text and len(existing_case.raw_text) > 50:
                progress(f"  [跳过] 已存在: {case_id}")
                return existing_case
            else:
                progress(f"  [重试] 删除失败记录: {case_id} ({existing_case.error})")
                json_path.unlink()
        except Exception:
            # 旧文件损坏，删除重建
            json_path.unlink(missing_ok=True)

    full_link = link_url if link_url.startswith("http") else urljoin(
        "https://www.csrc.gov.cn/", link_url
    )

    raw_text: Optional[str] = None
    pdf_url = ""
    doc_url = ""

    # 1) 直接 PDF：下载并提取文本（文本型 PDF 用 pypdf；扫描型留空）
    if ".pdf" in full_link.lower():
        pdf_url = full_link
        progress(f"  [PDF] 提取中: {title}")
        pdf_text = extract_text_from_pdf(full_link)
        if pdf_text and len(pdf_text) > 50:
            raw_text = pdf_text
            progress(f"  [PDF✓] {title} ({len(pdf_text)} 字符)")
        else:
            raw_text = ""
            logger.warning(f"  [PDF提取失败] {title} - 可能是扫描型 PDF 或提取失败")

    else:
        # 2) HTML 详情页
        soup = fetch_html_page(full_link)
        # 发现 PDF 附件链接时仅记录到 pdf_url，仍以 HTML 正文为主
        pdf_url = find_pdf_link_in_page(full_link, soup=soup) or ""

        html_text = extract_text_from_html(full_link, soup=soup)
        if html_text and len(html_text) > 50:
            raw_text = html_text
            progress(f"  [HTML✓] {title} ({len(html_text)} 字符)")
        else:
            # HTML 正文过短，依次尝试 Word 文档与 PDF 附件
            raw_text = html_text or ""
            doc_url = find_doc_link_in_page(full_link, soup=soup) or ""
            if doc_url:
                doc_text = extract_text_from_docx(doc_url)
                if doc_text and len(doc_text) > 50:
                    raw_text = doc_text
                    progress(f"  [Word✓] {title} ({len(doc_text)} 字符)")
                else:
                    logger.warning(f"  [Word提取失败] {title} - docx 文本提取失败")
            elif pdf_url:
                # HTML 无正文但找到 PDF 附件，尝试提取 PDF 文本
                pdf_text = extract_text_from_pdf(pdf_url)
                if pdf_text and len(pdf_text) > 50:
                    raw_text = pdf_text
                    progress(f"  [PDF✓] {title} ({len(pdf_text)} 字符)")
                else:
                    logger.warning(f"  [无内容] {title} - HTML无正文且 PDF 提取失败")
            else:
                logger.warning(f"  [无内容] {title} - HTML无有效正文且无 Word/PDF 附件链接")

    # 3) 基金内容确认（标题模糊时）
    is_fund = skip_fund_check
    fund_evidence = "标题含'基金'" if skip_fund_check else ""

    if not is_fund and raw_text:
        if is_fund_by_content(raw_text):
            is_fund = True
            fund_evidence = "正文含'基金'"
        else:
            progress(f"  [过滤] 内容非基金: {title}")
            return None
    elif not is_fund and not raw_text:
        # 无文本且标题未确认基金，无法判定，跳过
        progress(f"  [过滤] 无正文且标题未确认基金: {title}")
        return None

    # 4) 文号与受处罚主体提取
    document_number = extract_document_number(raw_text or "")
    punished_entities = extract_punished_entities(title, raw_text or "")

    # 5) 落盘
    case = CaseData(
        case_id=case_id,
        source_url=full_link,
        case_type=case_type,
        bureau=bureau_name,
        title=title,
        date=date_str,
        raw_text=raw_text or "",
        fetch_time=datetime.now().isoformat(),
        error="" if (raw_text and len(raw_text) > 50) else "未能提取到有效文本",
        is_fund_related=is_fund,
        fund_evidence=fund_evidence,
        document_number=document_number,
        punished_entities=punished_entities,
        pdf_url=pdf_url,
        doc_url=doc_url,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(asdict(case), f, ensure_ascii=False, indent=2)

    status = "✓" if (case.raw_text and len(case.raw_text) > 50) else "✗"
    progress(f"  [{status}] {case_id} | {len(case.raw_text)} 字符")

    return case


# ──────────────────────────── 列表页爬取 ────────────────────────────

def _parse_list_items_from_soup(soup: BeautifulSoup, base_url: str) -> List[Dict]:
    """从 CSRC 列表页 HTML 中解析案例条目。

    监管措施列表项格式：`<li><a href="...content.shtml">标题</a>日期</li>`
    行政处罚列表项格式（表格）：`<tr><td>序号</td><td><a href>标题</a></td>...</td><td>日期</td></tr>`

    Returns:
        [{link_url, title, date}, ...]  date 为 datetime.date
    """
    items: List[BeautifulSoup] = []

    # 1) 优先尝试 li 列表结构（监管措施页常见）
    for sel in ["ul.list li", "ul.news_list li", ".list-main li",
                "div.list ul li", "ul li", ".content li"]:
        found = soup.select(sel)
        if found:
            items = found
            break

    results: List[Dict] = []

    if items:
        for item in items:
            link_tag = item.find("a", href=True)
            if not link_tag:
                continue
            full_row_text = item.get_text(separator=" ", strip=True)
            raw_title = link_tag.get_text(strip=True)
            if not raw_title or len(raw_title) < 4:
                continue
            link_url = link_tag["href"]
            item_date = parse_date_from_text(full_row_text)
            if not item_date:
                continue
            results.append({
                "link_url": link_url,
                "title": raw_title,
                "date": item_date,
            })

    # 2) 表格结构（部分行政处罚页可能采用）
    if not results:
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link_tag = row.find("a", href=True)
            if not link_tag:
                continue
            raw_title = link_tag.get_text(strip=True)
            if not raw_title or len(raw_title) < 4:
                continue
            row_text = row.get_text(separator=" ", strip=True)
            item_date = parse_date_from_text(row_text)
            if not item_date:
                continue
            results.append({
                "link_url": link_tag["href"],
                "title": raw_title,
                "date": item_date,
            })

    # 规范化相对 URL
    for r in results:
        if not r["link_url"].startswith("http"):
            r["link_url"] = urljoin(base_url, r["link_url"])

    return results


def _filter_links_by_date(
    links: List[Dict],
    start_date: datetime.date,
    end_date: datetime.date,
) -> Tuple[List[Dict], bool]:
    """按日期过滤列表条目，返回 (保留条目, 是否应停止翻页)。

    列表按日期倒序排列：
        - 日期 > end_date：跳过
        - 日期 < start_date：跳过并触发停止翻页
    """
    keep: List[Dict] = []
    should_stop = False
    for link in links:
        d = link["date"]
        if d > end_date:
            continue
        if d < start_date:
            should_stop = True
            break
        keep.append(link)
    return keep, should_stop


def collect_measure_links(
    bureau: Bureau,
    start_date: datetime.date,
    end_date: datetime.date,
) -> List[Dict]:
    """爬取监管措施列表页，返回 [{link_url, title, date}, ...]。

    分页规则：
        第 1 页：common_list_gd.shtml
        第 N 页：common_list_gd_{N}.shtml（N≥2）
    遇到 404 或空列表时停止翻页。
    """
    base_url = bureau.measure_url
    if not base_url:
        logger.warning(f"[{bureau.name_en}] 未配置监管措施 URL，跳过")
        return []

    # 推断分页 URL 模板：将 common_list_gd.shtml 替换为 common_list_gd_{N}.shtml
    page_url_tpl = re.sub(
        r"common_list_gd\.shtml$",
        "common_list_gd_{N}.shtml",
        base_url,
    )

    session = SessionManager()
    results: List[Dict] = []
    page_index = 1
    should_stop = False
    consecutive_failures = 0

    while not should_stop:
        url = base_url if page_index == 1 else page_url_tpl.replace("{N}", str(page_index))
        progress(f"  [{bureau.name_en}/measure] 请求第 {page_index} 页...")

        page_ok = False
        for retry in range(LIST_PAGE_RETRIES):
            try:
                resp = session.get(url)
                if resp.status_code == 404:
                    progress(f"  [{bureau.name_en}] 第 {page_index} 页 404，翻页结束")
                    should_stop = True
                    page_ok = True
                    break
                resp.raise_for_status()
                if resp.encoding == "ISO-8859-1" or resp.encoding is None:
                    resp.encoding = resp.apparent_encoding or "utf-8"

                soup = BeautifulSoup(resp.text, "html.parser")
                items = _parse_list_items_from_soup(soup, url)

                if not items:
                    progress(f"  [{bureau.name_en}] 第 {page_index} 页无列表项，翻页结束")
                    should_stop = True
                    page_ok = True
                    break

                keep, stop = _filter_links_by_date(items, start_date, end_date)
                results.extend(keep)
                progress(f"  [{bureau.name_en}] 翻页: 第 {page_index} 页, 累计 {len(results)} 个案例")
                if stop:
                    progress(f"  [{bureau.name_en}] 遇到早于 {start_date} 的条目，停止翻页")
                    should_stop = True
                page_ok = True
                break

            except Exception as e:
                if retry < LIST_PAGE_RETRIES - 1:
                    wait = 3 * (retry + 1)
                    logger.warning(f"  列表页请求失败 (重试 {retry + 1}/{LIST_PAGE_RETRIES}): {e}，{wait}秒后重试...")
                    time.sleep(wait)
                else:
                    logger.error(f"  列表页处理出错 (已重试{LIST_PAGE_RETRIES}次): {e}")

        if should_stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(f"  第 {page_index} 页跳过 (连续失败 {consecutive_failures}/{MAX_CONSECUTIVE_PAGE_FAILURES})")
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                logger.error(f"  连续 {MAX_CONSECUTIVE_PAGE_FAILURES} 页失败，停止翻页")
                break
            page_index += 1
            time.sleep(DELAY_BETWEEN_PAGES)
            continue

        consecutive_failures = 0
        page_index += 1
        time.sleep(DELAY_BETWEEN_PAGES)

    return results


# 行政处罚列表 API 候选端点（用于 AJAX 动态加载页面）
_PENALTY_API_PATHS = [
    "/csrc/{path_code}/list.do",
    "/csrc/{path_code}/zfxxgk_zdgk/list.do",
    "/csrc/{path_code}/common_list.do",
]


# CSRC 官网 searchList API（render.js 中 table_ajax 调用）
# URL 模式：/searchList/{channelId}?_isAgg=true&_isJson=true&_pageSize=50&_template=index&page={N}
# 返回 JSON：{"channelName": "...", "data": {"total": N, "results": [{title,url,publishedTimeStr,...}]}}
_SEARCHLIST_API_BASE = "https://www.csrc.gov.cn/searchList/"


def _extract_channelid_from_url(url: str) -> str:
    """从带 channelid 查询参数的 URL 中提取 channelId。"""
    if not url:
        return ""
    qs = parse_qs(urlsplit(url).query)
    return qs.get("channelid", [""])[0]


def _try_searchlist_api(
    bureau: Bureau,
    start_date: datetime.date,
    end_date: datetime.date,
    max_pages: int = 50,
    penalty_url: str = "",
    channelid: str = "",
) -> Optional[List[Dict]]:
    """通过 searchList API 获取行政处罚案例列表。

    CSRC 官网的 common_list.shtml 页面通过 render.js 调用
    /searchList/{channelId} 接口动态加载列表。本函数直接调用该 API
    并按日期过滤、分页拉取。

    Args:
        bureau: 证监局配置。
        start_date: 起始日期（含）。
        end_date: 结束日期（含）。
        max_pages: 最大翻页数，防止异常情况下无限翻页。
        penalty_url: discover_penalty_url 发现的 URL（可能带 channelid）。
            优先于 bureau.penalty_url 使用。
        channelid: 显式传入的 channelid。优先于 bureau.penalty_channelid 使用。

    Returns:
        案例列表 [{link_url, title, date}, ...]；若 API 不可用返回 None。
    """
    # 优先使用显式传入的参数，其次从 bureau 配置读取，最后从 penalty_url 提取
    eff_url = penalty_url or bureau.penalty_url
    cid = channelid or bureau.penalty_channelid or _extract_channelid_from_url(eff_url)
    if not cid:
        logger.debug("  [searchList] 缺少 channelid，跳过")
        return None

    session = SessionManager()
    # 注意：CSRC 服务器会忽略 _pageSize 参数，实际每页固定返回 20 条。
    # 因此不能用 page * page_size >= total 判断是否取完，必须用实际返回条数。
    requested_page_size = 50
    results: List[Dict] = []
    raw_collected = 0  # 服务器实际返回的条目累计数（未经日期过滤）
    stop_for_date = False

    for page in range(1, max_pages + 1):
        api_url = (
            f"{_SEARCHLIST_API_BASE}{cid}"
            f"?_isAgg=true&_isJson=true&_pageSize={requested_page_size}"
            f"&_template=index&page={page}"
        )
        try:
            resp = session.get(
                api_url,
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning(f"  [searchList] 第 {page} 页 status={resp.status_code}")
                break
            ctype = resp.headers.get("Content-Type", "")
            if "json" not in ctype.lower():
                logger.warning(f"  [searchList] 第 {page} 页非 JSON 响应: {ctype}")
                break

            data = resp.json()
            ch_name = data.get("channelName", "")
            total = data.get("data", {}).get("total", 0)
            items = data.get("data", {}).get("results", []) or []
            if not items:
                progress(f"  [searchList] 第 {page} 页无结果，停止（栏目={ch_name}）")
                break

            raw_collected += len(items)

            if page == 1:
                progress(f"  [searchList] 命中栏目={ch_name}，总数={total}，每页返回{len(items)}条")

            for item in items:
                title = item.get("title") or item.get("name") or ""
                url = item.get("redirectUrl") or item.get("url") or ""
                date_str = (
                    item.get("publishedTimeStr")
                    or item.get("publishTime")
                    or item.get("publishDate")
                    or ""
                )
                if not title or not url:
                    continue
                item_date = parse_date_from_text(str(date_str))
                if not item_date:
                    continue
                if item_date < start_date:
                    stop_for_date = True
                    break
                if item_date > end_date:
                    continue
                # url 形如 //www.csrc.gov.cn/...，补全协议
                if url.startswith("//"):
                    url = "https:" + url
                elif not url.startswith("http"):
                    url = urljoin("https://www.csrc.gov.cn/", url)
                results.append({"link_url": url, "title": title, "date": item_date})

            progress(
                f"  [searchList] 第 {page} 页：本页{len(items)}条，累计原始{raw_collected}/{total}，"
                f"范围内{len(results)}个案例"
            )

            if stop_for_date:
                progress(f"  [searchList] 遇到早于 {start_date} 的条目，停止翻页")
                break
            # 用实际返回条数判断是否已取完（服务器忽略 _pageSize，每页固定20条）
            if raw_collected >= total:
                break
            # 不足一页也是最后一页
            if len(items) < 20:
                break
            time.sleep(DELAY_BETWEEN_PAGES)
        except Exception as e:
            logger.warning(f"  [searchList] 第 {page} 页异常: {e}")
            break

    return results if results else None


def _try_penalty_api(bureau: Bureau, start_date: datetime.date,
                     end_date: datetime.date,
                     penalty_url: str = "") -> Optional[List[Dict]]:
    """探测行政处罚后端 API，返回结果列表或 None。

    CSRC 行政处罚页通常为 AJAX 动态加载，这里尝试若干常见 API 路径。
    若全部失败则返回 None，由调用方决定回退策略。

    Args:
        bureau: 证监局配置。
        start_date: 起始日期。
        end_date: 结束日期。
        penalty_url: discover_penalty_url 发现的 URL，优先于 bureau.penalty_url。
    """
    eff_url = penalty_url or bureau.penalty_url
    if not eff_url:
        return None

    # 从 penalty_url 中解析 channelid 与基础路径
    parsed = urlsplit(eff_url)
    channelid = bureau.penalty_channelid or _extract_channelid_from_url(eff_url)
    base_path = parsed.path.rsplit("/", 1)[0]  # 去掉 zfxxgk_zdgk.shtml

    session = SessionManager()
    candidates: List[str] = []

    # 候选 1：在 penalty_url 同目录下尝试 list.do / query.do
    for ep in ["list.do", "query.do", "getList.do", "list.json"]:
        candidates.append(f"{parsed.scheme}://{parsed.netloc}{base_path}/{ep}")

    # 候选 2：使用 bureaus.py 中的 path_code 模式
    for tpl in _PENALTY_API_PATHS:
        candidates.append(
            f"{parsed.scheme}://{parsed.netloc}{tpl.format(path_code=bureau.path_code)}"
        )

    for api_url in candidates:
        for method in ("GET", "POST"):
            try:
                params = {"channelid": channelid, "page": 0, "size": 50}
                if method == "GET":
                    resp = session.get(api_url, params=params,
                                       headers={"X-Requested-With": "XMLHttpRequest"})
                else:
                    resp = session.post(api_url, json=params,
                                        headers={"X-Requested-With": "XMLHttpRequest"})
                if resp.status_code != 200:
                    continue
                ctype = resp.headers.get("Content-Type", "")
                if "json" not in ctype.lower():
                    continue
                data = resp.json()
                items = _parse_penalty_api_response(data, start_date, end_date)
                if items:
                    progress(f"  [行政处罚API] 命中 {api_url} ({method})，得到 {len(items)} 条")
                    return items
            except Exception:
                continue

    return None


def _parse_penalty_api_response(data: Dict, start_date: datetime.date,
                                end_date: datetime.date) -> List[Dict]:
    """解析行政处罚 API 返回的 JSON，提取案例条目。"""
    results: List[Dict] = []
    # 兼容多种字段命名
    items = (
        data.get("content")
        or data.get("list")
        or data.get("data")
        or data.get("rows")
        or []
    )
    if not isinstance(items, list):
        return results

    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or ""
        url = item.get("url") or item.get("linkUrl") or item.get("href") or ""
        date_str = (
            item.get("publishDate")
            or item.get("date")
            or item.get("publishdate")
            or item.get(" createDate")
            or ""
        )
        if not title or not url:
            continue
        item_date = parse_date_from_text(str(date_str))
        if not item_date:
            continue
        if item_date > end_date or item_date < start_date:
            continue
        if not url.startswith("http"):
            url = urljoin("https://www.csrc.gov.cn/", url)
        results.append({"link_url": url, "title": title, "date": item_date})

    return results


def collect_penalty_links(
    bureau: Bureau,
    start_date: datetime.date,
    end_date: datetime.date,
) -> List[Dict]:
    """爬取行政处罚列表页。

    优先尝试 HTML 解析；若 HTML 无列表数据（AJAX 动态加载），探测后端 API；
    若以上均失败，记录日志并跳过（不使用浏览器自动化，保持简单）。
    """
    # 必要时通过 discover_penalty_url 自动发现
    penalty_url = bureau.penalty_url
    if not penalty_url:
        try:
            penalty_url = discover_penalty_url(bureau)
        except Exception as e:
            logger.warning(f"[{bureau.name_en}] 行政处罚 URL 自动发现失败: {e}")
    if not penalty_url:
        logger.warning(f"[{bureau.name_en}/penalty] 未获取到行政处罚 URL，跳过")
        return []

    # 提取 channelid，供后续 searchList API 使用
    discovered_channelid = (
        bureau.penalty_channelid or _extract_channelid_from_url(penalty_url)
    )

    # 行政处罚分页 URL 模板：zfxxgk_zdgk_{N}.shtml（与监管措施同理）
    page_url_tpl = re.sub(r"zfxxgk_zdgk\.shtml", "zfxxgk_zdgk_{N}.shtml", penalty_url)

    session = SessionManager()
    results: List[Dict] = []
    page_index = 1
    should_stop = False
    consecutive_failures = 0
    html_tried = False

    while not should_stop:
        url = penalty_url if page_index == 1 else page_url_tpl.replace("{N}", str(page_index))
        progress(f"  [{bureau.name_en}/penalty] 请求第 {page_index} 页...")

        page_ok = False
        for retry in range(LIST_PAGE_RETRIES):
            try:
                resp = session.get(url)
                if resp.status_code == 404:
                    progress(f"  [{bureau.name_en}] 第 {page_index} 页 404，翻页结束")
                    should_stop = True
                    page_ok = True
                    break
                resp.raise_for_status()
                if resp.encoding == "ISO-8859-1" or resp.encoding is None:
                    resp.encoding = resp.apparent_encoding or "utf-8"

                soup = BeautifulSoup(resp.text, "html.parser")
                items = _parse_list_items_from_soup(soup, url)
                html_tried = True

                if items:
                    keep, stop = _filter_links_by_date(items, start_date, end_date)
                    results.extend(keep)
                    progress(f"  [{bureau.name_en}] 翻页: 第 {page_index} 页, 累计 {len(results)} 个案例")
                    if stop:
                        should_stop = True
                    page_ok = True
                    break

                # HTML 无列表项：可能是 AJAX 加载，仅在第一页探测 API
                if page_index == 1:
                    progress(f"  [{bureau.name_en}] HTML 无列表项，尝试 searchList API...")
                    # 优先使用 searchList API（CSRC 官网 render.js 调用的接口）
                    api_items = _try_searchlist_api(
                        bureau, start_date, end_date,
                        penalty_url=penalty_url, channelid=discovered_channelid,
                    )
                    if not api_items:
                        # 退化到旧的 list.do 探测
                        progress(f"  [{bureau.name_en}] searchList API 无结果，尝试 list.do 探测...")
                        api_items = _try_penalty_api(
                            bureau, start_date, end_date, penalty_url=penalty_url,
                        )
                    if api_items:
                        results.extend(api_items)
                        progress(f"  [{bureau.name_en}] [API] 共获取 {len(api_items)} 个案例")
                        page_ok = True
                        should_stop = True  # API 通常一次性返回，不再翻页
                        break
                    else:
                        logger.warning(f"  行政处罚 API 探测失败，停止该来源抓取")
                        should_stop = True
                        page_ok = True
                        break
                else:
                    # 后续页面无列表项视为结束
                    progress(f"  [{bureau.name_en}] 第 {page_index} 页无列表项，翻页结束")
                    should_stop = True
                    page_ok = True
                    break

            except Exception as e:
                if retry < LIST_PAGE_RETRIES - 1:
                    wait = 3 * (retry + 1)
                    logger.warning(f"  列表页请求失败 (重试 {retry + 1}/{LIST_PAGE_RETRIES}): {e}，{wait}秒后重试...")
                    time.sleep(wait)
                else:
                    logger.error(f"  列表页处理出错 (已重试{LIST_PAGE_RETRIES}次): {e}")

        if should_stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(f"  第 {page_index} 页跳过 (连续失败 {consecutive_failures}/{MAX_CONSECUTIVE_PAGE_FAILURES})")
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                logger.error(f"  连续 {MAX_CONSECUTIVE_PAGE_FAILURES} 页失败，停止翻页")
                break
            page_index += 1
            time.sleep(DELAY_BETWEEN_PAGES)
            continue

        consecutive_failures = 0
        page_index += 1
        time.sleep(DELAY_BETWEEN_PAGES)

    if not results and not html_tried:
        logger.warning(f"[{bureau.name_en}/penalty] 列表页与 API 均未获取到案例")

    return results


# ──────────────────────────── 批量抓取主流程 ────────────────────────────

def fetch_source(
    bureau: Bureau,
    case_type: str,
    start_date: datetime.date,
    end_date: datetime.date,
    output_dir: Path,
    index: CaseIndex,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> Tuple[int, int, int]:
    """抓取单个来源（局 × 类型）的全部案例。

    流程：列表爬取 → 标题预过滤 → 详情处理（含内容确认）→ 索引更新。

    Args:
        concurrency: 详情页并发处理线程数。1 表示串行（保留 DELAY_BETWEEN_CASES 节流），
            大于 1 时使用 ThreadPoolExecutor 并发抓取。列表页翻页始终串行。

    Returns:
        (success, fail, skipped) 三类计数。
    """
    # 规范化并发数到合法区间
    concurrency = max(MIN_CONCURRENCY, min(MAX_CONCURRENCY, int(concurrency)))
    source_key = CaseIndex.source_key(bureau.name_en, case_type)
    logger.info(f"{'=' * 20} {bureau.name_cn} / {CASE_TYPE_CN[case_type]} {'=' * 20}")

    output_dir.mkdir(parents=True, exist_ok=True)

    stats = index.get_stats(source_key)
    logger.info(f"  索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
                f"待处理 {stats['pending']}, 失败 {stats['failed']}, 跳过 {stats['skipped_not_fund']}")

    # 1) 刷新列表页
    logger.info("  从官网爬取案例列表 (列表页成本低，每次都刷新以确保完整性)...")
    if case_type == CASE_TYPE_MEASURE:
        case_links = collect_measure_links(bureau, start_date, end_date)
    else:
        case_links = collect_penalty_links(bureau, start_date, end_date)

    new_count = 0
    existing_urls = {item["link_url"] for item in index.get_source_links(source_key)}
    for link in case_links:
        if link["link_url"] not in existing_urls:
            new_count += 1
    index.update_source(source_key, case_links, last_page=0)
    logger.info(f"  列表页收集到 {len(case_links)} 个案例，其中 {new_count} 个为新发现")

    # 2) 标题预过滤：明确非基金的直接标记 skipped_not_fund
    pending_links = index.get_source_links(source_key)
    to_process: List[Dict] = []
    for item in pending_links:
        status = item.get("status", "pending")
        if status in ("pending", "failed"):
            verdict = is_fund_by_title(item.get("title", ""))
            if verdict is False:
                # 明确非基金，直接跳过
                if status != "skipped_not_fund":
                    index.mark_skipped(source_key, item["link_url"], "title_non_fund")
                continue
            to_process.append(item)
        # 已 done / skipped_not_fund 的不再处理

    if not to_process:
        logger.info(f"  {bureau.name_cn}/{CASE_TYPE_CN[case_type]}: 无待处理案例")
        final = index.get_stats(source_key)
        return 0, 0, final["skipped_not_fund"]

    logger.info(f"  待处理: {len(to_process)} 个 (已跳过非基金 {stats['skipped_not_fund']} 个)")
    if concurrency > 1:
        logger.info(f"  并发模式: {concurrency} 线程")
    else:
        logger.info(f"  串行模式 (详情页间隔 {DELAY_BETWEEN_CASES}s)")

    success = 0
    fail = 0
    skipped = 0

    def _process_one(idx: int, link_info: Dict) -> Tuple[str, str, Optional[CaseData], str]:
        """单个案例处理 worker。

        Returns:
            (status, link_url, case, error)
            status ∈ {"success", "fail", "skipped", "error"}
        """
        link_url = link_info["link_url"]
        raw_title = link_info["title"]
        item_date_str = link_info["date"]
        try:
            item_date = datetime.strptime(item_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            item_date_str = datetime.now().date().isoformat()

        # 标题是否已明确基金（决定 process_case 是否跳过内容确认）
        title_verdict = is_fund_by_title(raw_title)
        skip_fund_check = (title_verdict is True)

        try:
            case = process_case(
                link_url=link_url,
                title=raw_title,
                date_str=item_date_str,
                bureau_name=bureau.name_en,
                case_type=case_type,
                output_dir=output_dir,
                skip_fund_check=skip_fund_check,
            )
            if case is None:
                return "skipped", link_url, None, ""
            if case.raw_text and len(case.raw_text) > 50:
                return "success", link_url, case, ""
            return "fail", link_url, case, case.error or "未能提取到有效文本"
        except Exception as e:
            return "error", link_url, None, str(e)

    total = len(to_process)

    def _handle_result(status: str, link_url: str, error: str,
                       counts: Dict[str, int]) -> None:
        """根据 worker 返回状态更新索引与计数。"""
        if status == "success":
            counts["success"] += 1
            index.mark_done(source_key, link_url)
        elif status == "skipped":
            counts["skipped"] += 1
            index.mark_skipped(source_key, link_url, "content_non_fund")
        elif status == "fail":
            counts["fail"] += 1
            index.mark_failed(source_key, link_url, error)
        else:  # error
            logger.error(f"  处理异常: {error}")
            counts["fail"] += 1
            index.mark_failed(source_key, link_url, error)

    if concurrency <= 1:
        # 串行模式：保留 DELAY_BETWEEN_CASES 节流
        for i, link_info in enumerate(to_process, 1):
            raw_title = link_info["title"]
            progress(f"  [{i}/{total}] {raw_title}")
            status, link_url, _case, error = _process_one(i, link_info)
            counts = {"success": 0, "fail": 0, "skipped": 0}
            _handle_result(status, link_url, error, counts)
            success += counts["success"]
            fail += counts["fail"]
            skipped += counts["skipped"]
            if i < total:
                time.sleep(DELAY_BETWEEN_CASES)
    else:
        # 并发模式：ThreadPoolExecutor 处理详情页
        counts = {"success": 0, "fail": 0, "skipped": 0}
        completed = 0
        with ThreadPoolExecutor(max_workers=concurrency,
                                thread_name_prefix="csrc") as executor:
            futures = {
                executor.submit(_process_one, i, link_info): link_info
                for i, link_info in enumerate(to_process, 1)
            }
            for future in as_completed(futures):
                try:
                    status, link_url, _case, error = future.result()
                except Exception as e:
                    # worker 抛出未捕获异常的兜底
                    link_url = futures[future]["link_url"]
                    status, error = "error", f"worker异常: {e}"
                _handle_result(status, link_url, error, counts)
                completed += 1
                if completed % 20 == 0 or completed == total:
                    progress(f"  进度: {completed}/{total} "
                             f"(成功 {counts['success']}, 失败 {counts['fail']}, "
                             f"跳过 {counts['skipped']})")
        success = counts["success"]
        fail = counts["fail"]
        skipped = counts["skipped"]

    final_stats = index.get_stats(source_key)
    logger.info(f"  {bureau.name_cn}/{CASE_TYPE_CN[case_type]}: "
                f"本次成功 {success}, 失败 {fail}, 跳过 {skipped}")
    logger.info(f"  索引总计: {final_stats['done']} done / {final_stats['total']} total")
    return success, fail, skipped


# ──────────────────────────── 主函数 CLI ────────────────────────────

def _select_bureaus() -> List[Bureau]:
    """CLI 交互：选择抓取范围。"""
    print("\n请选择抓取范围:")
    print("1. 全部来源（会本部 + 36 家证监局）")
    print("2. 仅会本部")
    print("3. 指定证监局")
    choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"

    if choice == "2":
        return [b for b in BUREAUS if b.name_en == "HQ"]
    if choice == "3":
        print("\n可选证监局:")
        for i, b in enumerate(BUREAUS, 1):
            print(f"  {i:>2}. {b.name_cn} ({b.name_en})")
        idx_str = input("请输入局编号 (多个用逗号分隔): ").strip()
        selected: List[Bureau] = []
        for token in re.split(r"[,\s]+", idx_str):
            if not token:
                continue
            try:
                idx = int(token)
                if 1 <= idx <= len(BUREAUS):
                    selected.append(BUREAUS[idx - 1])
            except ValueError:
                # 也允许输入英文标识
                b = next((b for b in BUREAUS if b.name_en.lower() == token.lower()), None)
                if b:
                    selected.append(b)
        if not selected:
            print("未选择有效局，默认全部来源")
            return list(BUREAUS)
        return selected
    return list(BUREAUS)


def _select_case_types() -> List[str]:
    """CLI 交互：选择案例类型。"""
    print("\n请选择案例类型:")
    print("1. 全部（行政处罚 + 监管措施）")
    print("2. 仅行政处罚")
    print("3. 仅监管措施")
    choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"
    if choice == "2":
        return [CASE_TYPE_PENALTY]
    if choice == "3":
        return [CASE_TYPE_MEASURE]
    return [CASE_TYPE_PENALTY, CASE_TYPE_MEASURE]


def _select_concurrency() -> int:
    """CLI 交互：选择详情页并发线程数。

    单源内并发：列表页仍按源串行抓取，仅详情页用线程池并发处理。
    """
    print(f"\n请选择详情页并发数 (1-{MAX_CONCURRENCY}, 1=串行, 默认 {DEFAULT_CONCURRENCY}):")
    raw = input(f"  并发线程数 (默认 {DEFAULT_CONCURRENCY}): ").strip()
    if not raw:
        return DEFAULT_CONCURRENCY
    try:
        val = int(raw)
        if val < MIN_CONCURRENCY:
            print(f"  并发数不能小于 {MIN_CONCURRENCY}，已调整为 {MIN_CONCURRENCY}")
            val = MIN_CONCURRENCY
        elif val > MAX_CONCURRENCY:
            print(f"  并发数不能大于 {MAX_CONCURRENCY}，已调整为 {MAX_CONCURRENCY}")
            val = MAX_CONCURRENCY
        return val
    except ValueError:
        print(f"  无效输入，使用默认值 {DEFAULT_CONCURRENCY}")
        return DEFAULT_CONCURRENCY


def main() -> None:
    """CLI 入口：交互式选择参数后批量抓取。"""
    print("证监会基金相关行政处罚与监管措施数据库 抓取器 v1.0")
    print("=" * 60)

    start_time = time.time()

    # 抓取模式
    print("\n请选择抓取模式:")
    print("1. 抓取指定日期范围（默认 2022-01-01 至今）")
    print("2. 抓取单个案例 URL")
    print("3. 继续上次未完成的任务（断点续传）")

    choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"

    script_dir = Path(__file__).parent.resolve()
    output_base = script_dir / "cases"

    try:
        if choice == "1":
            # 指定日期范围
            default_start = DEFAULT_START_DATE.isoformat()
            default_end = datetime.now().date().isoformat()
            start_str = input(f"起始日期 (YYYY-MM-DD, 默认 {default_start}): ").strip() or default_start
            end_str = input(f"结束日期 (YYYY-MM-DD, 默认 {default_end}): ").strip() or default_end
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

            bureaus = _select_bureaus()
            case_types = _select_case_types()
            concurrency = _select_concurrency()

            index = CaseIndex(output_base)
            total_success = 0
            total_fail = 0
            total_skip = 0

            for bureau in bureaus:
                for ct in case_types:
                    src_dir = output_base / bureau.name_en / ct
                    s, f, k = fetch_source(
                        bureau=bureau,
                        case_type=ct,
                        start_date=start_date,
                        end_date=end_date,
                        output_dir=src_dir,
                        index=index,
                        concurrency=concurrency,
                    )
                    total_success += s
                    total_fail += f
                    total_skip += k

            SessionManager().close()
            elapsed = time.time() - start_time
            print(f"\n{'=' * 60}")
            print(f"全部完成: 成功 {total_success}, 失败 {total_fail}, 跳过非基金 {total_skip}")
            print(f"输出目录: {output_base}")
            print(f"并发数: {concurrency} (详情页), 耗时: {elapsed:.1f}秒")

        elif choice == "2":
            # 单个案例 URL
            url = input("请输入案例详情页 URL: ").strip()
            if not url:
                print("未输入 URL，退出。")
                return
            bureau_input = input("来源局英文标识 (如 HQ / Beijing, 默认 HQ): ").strip() or "HQ"
            ct_input = input("案例类型 (1=行政处罚 2=监管措施, 默认1): ").strip() or "1"
            if ct_input == "2":
                case_type = CASE_TYPE_MEASURE
            else:
                case_type = CASE_TYPE_PENALTY

            src_dir = output_base / bureau_input / case_type
            src_dir.mkdir(parents=True, exist_ok=True)

            date_str = datetime.now().date().isoformat()
            case = process_case(
                link_url=url,
                title="手动输入案例",
                date_str=date_str,
                bureau_name=bureau_input,
                case_type=case_type,
                output_dir=src_dir,
                skip_fund_check=True,
            )
            if case and case.raw_text:
                print(f"\n案例已保存至: {src_dir / f'{case.case_id}.json'}")
            else:
                print("\n案例处理失败。")

        elif choice == "3":
            # 断点续传
            index = CaseIndex(output_base)
            print("\n当前索引状态:")
            for src_key in index.data.get("sources", {}):
                stats = index.get_stats(src_key)
                print(f"  {src_key}: 总计 {stats['total']}, "
                      f"已完成 {stats['done']}, 待处理 {stats['pending']}, "
                      f"失败 {stats['failed']}, 跳过 {stats['skipped_not_fund']}")

            bureaus = _select_bureaus()
            case_types = _select_case_types()
            concurrency = _select_concurrency()

            total_success = 0
            total_fail = 0
            total_skip = 0

            for bureau in bureaus:
                for ct in case_types:
                    src_dir = output_base / bureau.name_en / ct
                    s, f, k = fetch_source(
                        bureau=bureau,
                        case_type=ct,
                        start_date=DEFAULT_START_DATE,
                        end_date=datetime.now().date(),
                        output_dir=src_dir,
                        index=index,
                        concurrency=concurrency,
                    )
                    total_success += s
                    total_fail += f
                    total_skip += k

            SessionManager().close()
            elapsed = time.time() - start_time
            print(f"\n{'=' * 60}")
            print(f"全部完成: 成功 {total_success}, 失败 {total_fail}, 跳过非基金 {total_skip}")
            print(f"输出目录: {output_base}")
            print(f"并发数: {concurrency} (详情页), 耗时: {elapsed:.1f}秒")

        else:
            print("无效选择，退出。")

    except KeyboardInterrupt:
        logger.info("\n用户中断，正在退出... (进度已保存到索引)")
        SessionManager().close()
    except Exception as e:
        logger.error(f"程序异常: {e}")
        traceback.print_exc()
        SessionManager().close()


if __name__ == "__main__":
    main()
