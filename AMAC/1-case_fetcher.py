"""
AMAC纪律处分案例抓取器 (case_fetcher.py)
功能：从中基协官网抓取纪律处分案例，自动识别内容类型（HTML全文/直接PDF/中间页→PDF），
     提取正文文本后保存为JSON，支持增量抓取和断点续传。
"""

import os
import re
import json
import time
import logging
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass, asdict
import threading

import requests
from requests.adapters import HTTPAdapter, Retry
from bs4 import BeautifulSoup

PROVIDER = "glm"

PROVIDER_CONFIG = {
    "glm": {
        "api_key_env": "ZHIPU_API_KEY",
        "api_key_default": "",
        "vision_model": "glm-4.6v-flash",
        "text_model": "glm-4.7-flash",
    },
    "mimo": {
        "api_key_env": "MIMO_API_KEY",
        "api_key_default": "",
        "base_url": "https://token-plan-sgp.xiaomimimo.com/v1",
        "vision_model": "mimo-v2.5",
        "text_model": "mimo-v2.5",
    },
    "zen": {
        "api_key_env": "OPENCODE_ZEN_API_KEY",
        "api_key_default": "",
        "base_url": "https://opencode.ai/zen/v1",
        "vision_model": "mimo-v2.5-free",
        "text_model": "x-preview-f-free",
        "text_models": {
            "x-preview-f-free": "Ox Alpha Free",
            "hy3-free": "Hy3 Free",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
        },
    },
}

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


# ──────────────────────────── 日志 ────────────────────────────

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("case_fetcher")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    return logger


logger = setup_logging()


# ──────────────────────────── 数据模型 ────────────────────────────

@dataclass
class CaseData:
    case_id: str
    source_url: str
    source_type: str  # "html" | "pdf_direct" | "pdf_embedded"
    category: str     # "scfjg" | "scfry"
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

class CaseIndex:
    """
    持久化的案例索引，记录所有已发现的案例链接和处理状态。
    文件结构: {output_dir}/_index.json
    {
        "last_updated": "2026-05-25T14:30:00",
        "categories": {
            "Institution": {
                "last_crawled_page": 5,
                "links": [
                    {"link_url": "...", "title": "...", "date": "2026-05-15", "status": "done"},
                    {"link_url": "...", "title": "...", "date": "2026-05-10", "status": "pending"},
                ]
            },
            "Personnel": { ... }
        }
    }
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.index_path = output_dir / "_index.json"
        self.data = self._load()

    def _load(self) -> Dict:
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"索引文件损坏，将重建: {e}")
        return {"last_updated": "", "categories": {}}

    def save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_category_links(self, category_key: str) -> List[Dict]:
        cat = self.data["categories"].get(category_key, {})
        return cat.get("links", [])

    def get_last_crawled_page(self, category_key: str) -> int:
        cat = self.data["categories"].get(category_key, {})
        return cat.get("last_crawled_page", -1)

    def update_category(self, category_key: str, links: List[Dict], last_page: int):
        existing = self.data["categories"].get(category_key, {})
        existing_links = {item["link_url"]: item for item in existing.get("links", [])}

        for link in links:
            url = link["link_url"]
            if url in existing_links:
                existing_links[url]["date"] = link["date"].isoformat() if hasattr(link["date"], "isoformat") else str(link["date"])
                existing_links[url]["title"] = link["title"]
            else:
                existing_links[url] = {
                    "link_url": url,
                    "title": link["title"],
                    "date": link["date"].isoformat() if hasattr(link["date"], "isoformat") else str(link["date"]),
                    "status": "pending",
                }

        existing["links"] = list(existing_links.values())
        existing["last_crawled_page"] = last_page
        self.data["categories"][category_key] = existing
        self.save()

    def mark_done(self, category_key: str, link_url: str):
        cat = self.data["categories"].get(category_key, {})
        for item in cat.get("links", []):
            if item["link_url"] == link_url:
                item["status"] = "done"
                break
        self.save()

    def mark_failed(self, category_key: str, link_url: str, error: str = ""):
        cat = self.data["categories"].get(category_key, {})
        for item in cat.get("links", []):
            if item["link_url"] == link_url:
                item["status"] = "failed"
                item["error"] = error
                break
        self.save()

    def get_pending_links(self, category_key: str) -> List[Dict]:
        links = self.get_category_links(category_key)
        return [item for item in links if item.get("status") == "pending"]

    def get_stats(self, category_key: str) -> Dict[str, int]:
        links = self.get_category_links(category_key)
        stats = {"total": len(links), "done": 0, "pending": 0, "failed": 0}
        for item in links:
            s = item.get("status", "pending")
            if s in stats:
                stats[s] += 1
        return stats


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


_client_cache = {}


def get_client(provider=None):
    if provider is None:
        provider = PROVIDER
    if provider in _client_cache:
        return _client_cache[provider]

    config = PROVIDER_CONFIG[provider]
    api_key = os.environ.get(config["api_key_env"], config.get("api_key_default", ""))
    client = None

    if provider == "mimo":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"MiMo客户端已初始化，模型: {config['text_model']}")
    elif provider == "zen":
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=config["base_url"])
        logger.info(f"OpenCode Zen客户端已初始化，模型: {config['text_model']}")
    else:
        try:
            from zai import ZhipuAiClient
            client = ZhipuAiClient(api_key=api_key)
            logger.info(f"智谱AI客户端已初始化 (zai.ZhipuAiClient)")
        except ImportError:
            pass
        if client is None:
            try:
                from zhipuai import ZhipuAI
                client = ZhipuAI(api_key=api_key)
                logger.info(f"智谱AI客户端已初始化 (zhipuai.ZhipuAI)")
            except ImportError:
                pass
        if client is None:
            raise ImportError("无法导入智谱AI客户端库，请安装: pip install zai-sdk 或 pip install zhipuai")

    _client_cache[provider] = client
    return client


def llm_chat(messages, model=None, max_tokens=2000, temperature=0.1,
             thinking=None, provider=None, stream=False, timeout=None):
    if provider is None:
        provider = PROVIDER
    if model is None:
        model = PROVIDER_CONFIG[provider]["text_model"]

    client = get_client(provider)

    if provider == "mimo":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.95,
            "stream": stream,
        }
    elif provider == "zen":
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
    else:
        kwargs = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if thinking:
            kwargs["thinking"] = thinking

    if timeout is not None:
        kwargs["timeout"] = timeout

    return client.chat.completions.create(**kwargs)


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


def parse_date_from_text(text: str) -> Optional[datetime.date]:
    m = re.search(r"(\d{4})\s*-\s*(\d{2})\s*-\s*(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            pass
    return None


def get_last_quarter_range() -> Tuple[datetime.date, datetime.date]:
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

def fetch_html_page(html_url: str) -> Optional[BeautifulSoup]:
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

def extract_text_from_html(html_url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
    """从HTML详情页提取纪律处分决定书正文"""
    try:
        if soup is None:
            soup = fetch_html_page(html_url)
        if soup is None:
            return None

        content_area = (
            soup.find("div", class_="TRS_Editor")
            or soup.find("div", class_="Custom_UnionStyle")
            or soup.find("div", class_=re.compile(r"detail|article|content|text", re.I))
            or soup.find("div", id=re.compile(r"detail|article|content", re.I))
            or soup.find("div", class_="TRAIS-info-content")
        )
        if not content_area:
            main_div = soup.find("div", class_=re.compile(r"right|main"))
            if main_div:
                content_area = main_div

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

def find_pdf_link_in_page(page_url: str, soup: Optional[BeautifulSoup] = None) -> Optional[str]:
    """从HTML页查找内嵌的PDF链接（优先查找附件下载区，避免误取侧边栏链接）"""
    try:
        if soup is None:
            soup = fetch_html_page(page_url)
        if soup is None:
            return None

        # 1) 优先在 class="attachment-down" 的容器中查找
        for container in soup.find_all("div", class_="attachment-down"):
            for link in container.find_all("a", href=True):
                href = link["href"]
                if ".pdf" in href.lower():
                    pdf_url = urljoin(page_url, href)
                    logger.info(f"  [附件区PDF] {pdf_url}")
                    return pdf_url

        # 2) 在包含"附件"文字的标题/段落附近查找
        for heading in soup.find_all(string=re.compile(r"附件.*下载|下载.*附件")):
            parent_div = heading.find_parent("div")
            if parent_div:
                for link in parent_div.find_all("a", href=True):
                    href = link["href"]
                    if ".pdf" in href.lower():
                        pdf_url = urljoin(page_url, href)
                        logger.info(f"  [附件区PDF] {pdf_url}")
                        return pdf_url

        # 3) 在 class 含 fujian/attachment/download 的容器中查找
        for container in soup.find_all("div", class_=re.compile(r"fujian|attachment|download|accessory", re.I)):
            for link in container.find_all("a", href=True):
                href = link["href"]
                if ".pdf" in href.lower():
                    pdf_url = urljoin(page_url, href)
                    logger.info(f"  [下载区PDF] {pdf_url}")
                    return pdf_url

        # 4) 全页面查找，但过滤掉侧边栏链接（路径含 hydj/xwfb/hdjl/hyyj/sjtj 等非纪律处分路径）
        sidebar_prefixes = ("hydj", "xwfb", "hdjl", "hyyj", "sjtj")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if ".pdf" not in href.lower():
                continue
            parts = href.replace("\\", "/").split("/")
            if any(p in parts for p in sidebar_prefixes):
                continue
            pdf_url = urljoin(page_url, href)
            logger.info(f"  [页面PDF] {pdf_url}")
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


# ──────────────────────────── 机构类型查询 ────────────────────────────

AMAC_MANAGER_API = "https://gs.amac.org.cn/amac-infodisc/api/pof/manager/query"
AMAC_MANAGER_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Host": "gs.amac.org.cn",
    "Origin": "https://gs.amac.org.cn",
    "Referer": "https://gs.amac.org.cn/amac-infodisc/res/pof/manager/managerList.html",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

AMAC_CANCELLED_API = "https://gs.amac.org.cn/amac-infodisc/api/cancelled/manager"
AMAC_CANCELLED_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "Host": "gs.amac.org.cn",
    "Origin": "https://gs.amac.org.cn",
    "Referer": "https://gs.amac.org.cn/amac-infodisc/res/cancelled/manager/index.html",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}
AMAC_CANCELLED_DETAIL_URL = "https://gs.amac.org.cn/amac-infodisc/res/cancelled/manager"

ORG_TYPE_VALUES = [
    "私募证券投资基金管理人",
    "私募股权、创业投资基金管理人",
    "私募股权投资基金管理人",
    "创业投资基金管理人",
    "其他私募投资基金管理人",
    "私募资产配置类管理人",
]


class OrgTypeCache:
    """机构类型本地缓存，避免重复查询API或调用LLM"""

    def __init__(self, output_dir: Path):
        self.cache_path = output_dir / "_org_type_cache.json"
        self.data: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get(self, org_name: str) -> Optional[str]:
        return self.data.get(org_name)

    def set(self, org_name: str, org_type: str):
        self.data[org_name] = org_type
        self.save()


def extract_org_name_from_title(title: str) -> Optional[str]:
    """从纪律处分标题中提取机构名称"""
    patterns = [
        r"关于对[《]?([^》]+?)[》]?(?:的)?(?:纪律处分|撤销|注销|暂停|取消)",
        r"关于对[《]?([^》]+?)[》]?的",
    ]
    for pattern in patterns:
        m = re.search(pattern, title)
        if m:
            name = m.group(1).strip()
            for suffix in ["的纪律处分决定书", "纪律处分决定书", "的决定书", "决定书",
                           "送达公告", "（送达公告）", "(送达公告)",
                           "事先告知书", "复核决定书"]:
                name = name.replace(suffix, "")
            name = name.rstrip("的、，,")
            if len(name) >= 4:
                return name

    m = re.search(r"[（(]((?:[^()（）]|[（(][^)）]*[)）])+)[)）]", title)
    if m:
        inner = m.group(1).strip()
        parts = re.split(r"[、，,]", inner)
        for part in parts:
            part = part.strip()
            if re.search(r"(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本|投资)", part):
                if len(part) >= 4:
                    return part

    m = re.search(r"([^\s,，、（）\(\)]+(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本))", title)
    if m:
        name = m.group(1).strip()
        if len(name) >= 4:
            return name
    return None


def query_org_type_from_amac(org_name: str) -> Optional[str]:
    """从中基协活跃管理人API查询机构类型"""
    if len(org_name) < 4:
        return None

    generic_suffixes = ["有限公司", "股份有限公司", "投资有限公司", "管理有限公司",
                        "基金管理有限公司", "资本管理有限公司", "资产管理有限公司"]
    if org_name in generic_suffixes:
        return None

    try:
        payload = {
            "keyword": org_name,
            "establishDate": {"from": "1900-01-01", "to": "9999-01-01"},
            "registerDate": {"from": "1900-01-01", "to": "9999-01-01"},
        }
        resp = requests.post(
            AMAC_MANAGER_API + "?page=0&size=10",
            json=payload,
            headers=AMAC_MANAGER_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"  [机构类型API] 查询失败 status={resp.status_code}")
            return None

        data = resp.json()
        total = data.get("totalElements", 0)
        if total == 0 or "content" not in data or len(data["content"]) == 0:
            return None

        for item in data["content"]:
            item_name = item.get("managerName", "")
            if org_name == item_name:
                org_type = item.get("primaryInvestType", "")
                if org_type:
                    logger.info(f"  [机构类型API] {org_name} → {org_type}")
                    return org_type

        for item in data["content"]:
            item_name = item.get("managerName", "")
            if org_name in item_name or item_name in org_name:
                org_type = item.get("primaryInvestType", "")
                if org_type:
                    logger.info(f"  [机构类型API] {org_name} → {org_type} (包含匹配)")
                    return org_type

        return None
    except Exception as e:
        logger.warning(f"  [机构类型API] 查询异常: {e}")
        return None


CANCELLED_API_MAX_RETRIES = 3
CANCELLED_API_RETRY_DELAY = 3


def query_org_type_from_amac_cancelled(org_name: str) -> Optional[str]:
    """从中基协已注销管理人API查询机构类型（注销前登记类型）

    搜索API受WAF保护，脚本调用大部分时候返回400，偶尔能成功。
    采用重试策略：最多尝试 CANCELLED_API_MAX_RETRIES 次，每次间隔 CANCELLED_API_RETRY_DELAY 秒。
    一旦搜索API成功返回结果，后续的详情页请求是稳定的。
    """
    if len(org_name) < 4:
        return None

    generic_suffixes = ["有限公司", "股份有限公司", "投资有限公司", "管理有限公司",
                        "基金管理有限公司", "资本管理有限公司", "资产管理有限公司"]
    if org_name in generic_suffixes:
        return None

    import random as _rand

    for attempt in range(1, CANCELLED_API_MAX_RETRIES + 1):
        try:
            rand_val = _rand.random()
            url = f"{AMAC_CANCELLED_API}?rand={rand_val}&page=0&size=10"
            resp = requests.post(
                url,
                json={"keyword": org_name},
                headers=AMAC_CANCELLED_HEADERS,
                timeout=15,
            )
            if resp.status_code != 200:
                logger.debug(
                    "  [已注销API] 第%d次查询失败 status=%d", attempt, resp.status_code,
                )
                if attempt < CANCELLED_API_MAX_RETRIES:
                    time.sleep(CANCELLED_API_RETRY_DELAY)
                continue

            data = resp.json()
            total = data.get("totalElements", 0)
            if total == 0 or "content" not in data or len(data["content"]) == 0:
                return None

            target_item = None
            for item in data["content"]:
                item_name = item.get("orgName", "")
                clean_name = re.sub(r"<[^>]+>", "", item_name)
                if org_name == clean_name:
                    target_item = item
                    break

            if not target_item:
                for item in data["content"]:
                    item_name = item.get("orgName", "")
                    clean_name = re.sub(r"<[^>]+>", "", item_name)
                    if org_name in clean_name or clean_name in org_name:
                        target_item = item
                        break

            if not target_item:
                return None

            tenant_id = target_item.get("userTenantId", "")
            if not tenant_id:
                return None

            detail_url = f"{AMAC_CANCELLED_DETAIL_URL}/{tenant_id}.html"
            detail_resp = requests.get(
                detail_url,
                headers={"User-Agent": AMAC_CANCELLED_HEADERS["User-Agent"]},
                timeout=15,
            )
            if detail_resp.status_code != 200:
                logger.debug("  [已注销详情] 页面获取失败 status=%d", detail_resp.status_code)
                return None

            detail_resp.encoding = "utf-8"
            m = re.search(r"机构类型.*?<td[^>]*>(.*?)</td>", detail_resp.text, re.S)
            if m:
                org_type = m.group(1).strip()
                if org_type:
                    logger.info("  [已注销API] %s → %s", org_name, org_type)
                    return org_type

            return None
        except Exception as e:
            logger.debug("  [已注销API] 第%d次查询异常: %s", attempt, e)
            if attempt < CANCELLED_API_MAX_RETRIES:
                time.sleep(CANCELLED_API_RETRY_DELAY)

    return None


def extract_org_type_from_text(org_name: str, raw_text: str) -> Optional[str]:
    """从案例正文中正则匹配机构类型（仅当正文恰好包含类型字符串时）"""
    if not raw_text or len(raw_text) < 100:
        return None

    snippet = raw_text[:3000]

    for org_type in ORG_TYPE_VALUES:
        if org_type in snippet:
            logger.info(f"  [机构类型文本] {org_name} → {org_type} (正则匹配)")
            return org_type

    return None


def extract_full_org_name_from_text(short_name: Optional[str], raw_text: str) -> Optional[str]:
    """从正文正则提取机构完整名称（当标题只有简称时使用）"""
    if not raw_text or len(raw_text) < 10:
        return None

    snippet = raw_text[:3000]

    if short_name:
        escaped = re.escape(short_name)
        m = re.search(
            r"([^，,：:；;\n]+)（以下简称" + escaped + r"）",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            for prefix in ["申请人：", "被申请人：", "申请人:", "被申请人:",
                           "当事人：", "当事人:", "被处分机构：", "被处分机构:"]:
                if name.startswith(prefix):
                    name = name[len(prefix):].strip()
            if len(name) >= 4 and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", name):
                return name

    for prefix in ["申请人", "被申请人", "被处分机构", "当事人"]:
        m = re.search(
            prefix + r"[：:]\s*([^\n,，。；;（(]+?(?:公司|企业|有限|合伙|事务所|集团)[^\n]*?)(?:[，,。\n（(]|$)",
            snippet,
        )
        if m:
            name = m.group(1).strip()
            if len(name) >= 4:
                return name

    for prefix in ["申请人", "被申请人", "被处分机构", "当事人"]:
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
        for prefix in ["申请人：", "被申请人：", "申请人:", "被申请人:",
                       "当事人：", "当事人:", "被处分机构：", "被处分机构:"]:
            if name.startswith(prefix):
                name = name[len(prefix):].strip()
        if len(name) >= 4:
            return name

    return None


def extract_org_name_from_text_llm(title: str, raw_text: str) -> Optional[str]:
    """使用LLM从正文提取受处分机构完整名称（降级方案）"""
    if not raw_text or len(raw_text) < 100:
        return None

    snippet = raw_text[:2000]
    try:
        prompt = (
            "请从以下纪律处分决定书文本中，提取被处分机构的完整注册名称。\n"
            "请只输出机构完整名称，不要输出其他内容。如果文本中没有提及，请输出\"未知\"。\n\n"
            "文本内容：\n" + snippet
        )
        messages = [{"role": "user", "content": prompt}]
        response = llm_chat(messages=messages, max_tokens=100, temperature=0.1, timeout=30)
        if hasattr(response, "choices") and len(response.choices) > 0:
            result = response.choices[0].message.content.strip()
            if result and result != "未知" and len(result) >= 4:
                return result
    except Exception as e:
        logger.warning(f"  [机构名称LLM] 提取异常: {e}")
    return None


def extract_punished_entity(title: str, raw_text: str, category_key: str) -> str:
    """提取受处分机构的完整名称，三级策略：标题→正则→LLM"""
    if category_key != "Institution":
        return ""

    title_name = extract_org_name_from_title(title)

    if title_name and re.search(r"(?:公司|企业|有限|合伙|事务所|集团)", title_name):
        return title_name

    if raw_text:
        full_name = extract_full_org_name_from_text(title_name, raw_text)
        if full_name:
            logger.info(f"  [机构名称正则] {title_name or '?'} → {full_name}")
            return full_name

    if raw_text and len(raw_text) >= 100:
        full_name = extract_org_name_from_text_llm(title, raw_text)
        if full_name:
            logger.info(f"  [机构名称LLM] {title_name or '?'} → {full_name}")
            return full_name

    return title_name or ""


def resolve_org_type(
    title: str,
    raw_text: str,
    category_key: str,
    cache: Optional[OrgTypeCache] = None,
    punished_entity: str = "",
) -> str:
    """解析机构类型，四级降级：缓存 → 活跃API → 已注销API → 正文正则"""
    if category_key != "Institution":
        return ""

    org_name = punished_entity or extract_org_name_from_title(title)
    if not org_name:
        logger.debug(f"  [机构类型] 无法提取机构名: {title}")
        return ""

    if cache:
        cached = cache.get(org_name)
        if cached:
            logger.info(f"  [机构类型缓存] {org_name} → {cached}")
            return cached

    org_type = query_org_type_from_amac(org_name)

    if not org_type:
        org_type = query_org_type_from_amac_cancelled(org_name)

    if not org_type:
        org_type = extract_org_type_from_text(org_name, raw_text)

    if org_type and cache:
        cache.set(org_name, org_type)

    return org_type or ""


# ──────────────────────────── PDF → 文本 (直接传URL给模型) ────────────────────────────

def extract_text_from_pdf_url(pdf_url: str) -> Optional[str]:
    """将PDF的URL传给视觉模型，让模型识别并输出全文"""
    vision_provider = PROVIDER
    vision_model = PROVIDER_CONFIG[vision_provider]["vision_model"]

    for attempt in range(OCR_MAX_RETRIES):
        try:
            logger.info(f"  [PDF→文本] 请求模型识别... (尝试 {attempt + 1}/{OCR_MAX_RETRIES})")

            if vision_provider in ("mimo", "zen"):
                content_parts = [
                    {
                        "type": "image_url",
                        "image_url": {"url": pdf_url},
                    },
                    {
                        "type": "text",
                        "text": (
                            "请完整、逐字逐句地识别并输出这份PDF文档的全部文字内容。"
                            "不要省略任何段落，不要做归纳总结，"
                            "保持原文的结构和格式，包括标题、编号、落款等。"
                            "如果遇到印章或手写文字，也请尽量识别。"
                        ),
                    },
                ]
            else:
                content_parts = [
                    {
                        "type": "file_url",
                        "file_url": {"url": pdf_url},
                    },
                    {
                        "type": "text",
                        "text": (
                            "请完整、逐字逐句地识别并输出这份PDF文档的全部文字内容。"
                            "不要省略任何段落，不要做归纳总结，"
                            "保持原文的结构和格式，包括标题、编号、落款等。"
                            "如果遇到印章或手写文字，也请尽量识别。"
                        ),
                    },
                ]

            messages = [
                {
                    "role": "user",
                    "content": content_parts,
                }
            ]

            response = llm_chat(
                messages=messages,
                model=vision_model,
                max_tokens=4000,
                temperature=0.1,
                thinking={"type": "enabled"} if vision_provider == "glm" else None,
                provider=vision_provider,
            )

            if hasattr(response, "choices") and len(response.choices) > 0:
                text = response.choices[0].message.content
                logger.info(f"  [PDF→文本] 识别完成，长度: {len(text)} 字符")
                return text
            else:
                logger.warning(f"  [PDF→文本] 响应结构异常")
                return None

        except Exception as e:
            logger.warning(f"  [PDF→文本] 第 {attempt + 1} 次失败: {e}")
            if attempt < OCR_MAX_RETRIES - 1:
                wait = 3 * (attempt + 1)
                logger.info(f"  [PDF→文本] {wait}秒后重试...")
                time.sleep(wait)

    return None


# ──────────────────────────── 单案例处理 ────────────────────────────

def process_case(
    link_url: str,
    raw_title: str,
    item_date: datetime.date,
    category_key: str,
    output_dir: Path,
    org_type_cache: Optional[OrgTypeCache] = None,
) -> Optional[CaseData]:
    """处理单个案例链接，返回CaseData或None"""

    case_id = extract_id_from_url(link_url)
    json_path = output_dir / f"{case_id}.json"

    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
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

    full_link = link_url if link_url.startswith("http") else urljoin(
        CATEGORIES[category_key]["url"], link_url
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
                    with open(json_path, "r", encoding="utf-8") as f:
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
    org_type = resolve_org_type(raw_title, raw_text or "", category_key, org_type_cache, punished_entity)

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

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(asdict(case), f, ensure_ascii=False, indent=2)

    status = "✓" if case.ocr_success else "✗"
    logger.info(f"  [{status}] {case_id} | {source_type} | {len(case.raw_text)} 字符")

    return case


# ──────────────────────────── 列表页爬取 ────────────────────────────

def collect_case_links(
    category_key: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> List[Dict]:
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
                items = []
                for sel in selectors:
                    found = soup.select(sel)
                    if found:
                        items = found
                        break

                if not items:
                    main_area = soup.find("div", class_=re.compile(r"right|main"))
                    if main_area:
                        items = [
                            li for li in main_area.find_all("li")
                            if len(li.get_text(strip=True)) > 10
                        ]

                if not items:
                    logger.warning("列表页无列表结构，停止翻页")
                    should_stop = True
                    page_ok = True
                    break

                page_count = 0
                for item in items:
                    link_tag = item.find("a", href=True)
                    if not link_tag:
                        continue

                    full_row_text = item.get_text(strip=True)
                    raw_title = link_tag.get_text(strip=True)
                    link_url = link_tag["href"]

                    item_date = parse_date_from_text(full_row_text)
                    if not item_date:
                        continue

                    if item_date > end_date:
                        continue
                    if item_date < start_date:
                        logger.info(f"遇到过期日期 {item_date}，停止翻页")
                        should_stop = True
                        break

                    results.append({
                        "link_url": link_url,
                        "title": raw_title,
                        "date": item_date,
                    })
                    page_count += 1

                logger.info(f"  第 {page_index + 1} 页收集到 {page_count} 个案例")
                page_ok = True
                break

            except Exception as e:
                if retry < LIST_PAGE_RETRIES - 1:
                    wait = 3 * (retry + 1)
                    logger.warning(f"列表页请求失败 (重试 {retry + 1}/{LIST_PAGE_RETRIES}): {e}，{wait}秒后重试...")
                    time.sleep(wait)
                else:
                    logger.error(f"列表页处理出错 (已重试{LIST_PAGE_RETRIES}次): {e}")

        if should_stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(f"第 {page_index + 1} 页跳过 (连续失败 {consecutive_failures}/{MAX_CONSECUTIVE_PAGE_FAILURES})")
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
    start_date: datetime.date,
    end_date: datetime.date,
    output_dir: Path,
    index: CaseIndex,
    org_type_cache: Optional[OrgTypeCache] = None,
) -> Tuple[int, int]:
    """抓取一个分类下的所有案例"""
    config = CATEGORIES[category_key]
    logger.info(f"{'='*20} {config['name']} {'='*20}")

    output_dir.mkdir(parents=True, exist_ok=True)

    stats = index.get_stats(category_key)
    logger.info(f"索引状态: 总计 {stats['total']}, 已完成 {stats['done']}, "
                f"待处理 {stats['pending']}, 失败 {stats['failed']}")

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
    failed_links = [item for item in index.get_category_links(category_key)
                    if item.get("status") == "failed"]
    to_process = pending + failed_links

    if not to_process:
        logger.info(f"{config['name']}: 无待处理案例")
        return stats["done"], 0

    logger.info(f"待处理: {len(pending)} pending + {len(failed_links)} failed = {len(to_process)} 个")

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


def _zen_pick_text_model():
    """选择 OpenCode Zen 文本模型（Ox Alpha Free / Hy3 Free / DeepSeek V4 Flash）"""
    tm_cfg = PROVIDER_CONFIG["zen"]
    options = list(tm_cfg["text_models"].items())
    print("\n请选择OpenCode Zen文本模型:")
    for i, (mid, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    choice = input(f"请输入选择 (1-{len(options)}, 默认1): ").strip() or "1"
    try:
        idx = max(0, min(len(options) - 1, int(choice) - 1))
    except ValueError:
        idx = 0
    tm_cfg["text_model"] = options[idx][0]
    logger.info(f"已选择文本模型: {options[idx][1]} ({options[idx][0]})")


# ──────────────────────────── 主函数 ────────────────────────────

def main():
    global PROVIDER

    print("AMAC纪律处分案例抓取器 v1.5")
    print("=" * 60)

    print("\n请选择模型提供者:")
    print("1. 智谱AI (GLM)")
    print("2. 小米 (MiMo)")
    print("3. OpenCode Zen (免费模型)")
    provider_choice = input("请输入选择 (1-3, 默认1): ").strip() or "1"
    if provider_choice == "2":
        PROVIDER = "mimo"
        logger.info("已选择MiMo模型提供者")
    elif provider_choice == "3":
        PROVIDER = "zen"
        logger.info("已选择OpenCode Zen模型提供者")
        _zen_pick_text_model()
    else:
        logger.info("已选择智谱AI模型提供者")

    start_time = time.time()

    print("\n请选择抓取模式:")
    print("1. 抓取上一季度案例 (自动计算日期范围)")
    print("2. 抓取指定日期范围的案例")
    print("3. 抓取单个案例URL")
    print("4. 继续上次未完成的任务 (断点续传)")

    choice = input("请输入选择 (1-4, 默认1): ").strip() or "1"

    script_dir = Path(__file__).parent.resolve()
    output_base = script_dir / "cases"
    org_type_cache = OrgTypeCache(output_base)

    try:
        if choice == "1":
            start_date, end_date = get_last_quarter_range()

        elif choice == "2":
            start_str = input("起始日期 (YYYY-MM-DD): ").strip()
            end_str = input("结束日期 (YYYY-MM-DD): ").strip()
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

        elif choice == "3":
            url = input("请输入案例URL: ").strip()
            if not url:
                print("未输入URL，退出。")
                return
            output_base.mkdir(parents=True, exist_ok=True)
            index = CaseIndex(output_base)
            case = process_case(
                link_url=url,
                raw_title="手动输入案例",
                item_date=datetime.now().date(),
                category_key="Institution",
                output_dir=output_base,
                org_type_cache=org_type_cache,
            )
            if case and case.ocr_success:
                print(f"\n案例已保存至: {output_base / f'{case.case_id}.json'}")
            else:
                print("\n案例处理失败。")
            return

        elif choice == "4":
            index = CaseIndex(output_base)
            print("\n当前索引状态:")
            for cat_key in CATEGORIES:
                stats = index.get_stats(cat_key)
                print(f"  {CATEGORIES[cat_key]['name']}: "
                      f"总计 {stats['total']}, 已完成 {stats['done']}, "
                      f"待处理 {stats['pending']}, 失败 {stats['failed']}")

            total_success = 0
            total_fail = 0

            for cat_key in CATEGORIES:
                cat_dir = output_base / cat_key.lower()
                s, f = fetch_category(
                    cat_key, datetime.min.date(), datetime.max.date(),
                    cat_dir, index, org_type_cache,
                )
                total_success += s
                total_fail += f

            SessionManager().close()
            elapsed = time.time() - start_time
            print(f"\n{'='*60}")
            print(f"全部完成: 成功 {total_success}, 失败 {total_fail}")
            print(f"输出目录: {output_base}")
            print(f"耗时: {elapsed:.1f}秒")
            return

        else:
            print("无效选择，退出。")
            return

        # 模式1/2：批量抓取
        index = CaseIndex(output_base)
        total_success = 0
        total_fail = 0

        for cat_key in CATEGORIES:
            cat_dir = output_base / cat_key.lower()
            s, f = fetch_category(cat_key, start_date, end_date, cat_dir, index, org_type_cache)
            total_success += s
            total_fail += f

        SessionManager().close()

        elapsed = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"全部完成: 成功 {total_success}, 失败 {total_fail}")
        print(f"输出目录: {output_base}")
        print(f"耗时: {elapsed:.1f}秒")

    except KeyboardInterrupt:
        logger.info("\n用户中断，正在退出... (进度已保存到索引)")
        SessionManager().close()
    except Exception as e:
        logger.error(f"程序异常: {e}")
        traceback.print_exc()
        SessionManager().close()


if __name__ == "__main__":
    main()
