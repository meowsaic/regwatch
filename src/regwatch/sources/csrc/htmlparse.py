"""CSRC 详情页的 HTML 获取与正文 / 附件链接提取。

附件链接发现复用 :mod:`regwatch.sources.htmlparse` 的共享实现，
此处只保留 CSRC 特有的侧边栏过滤前缀与正文提取参数。
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from ...logging_setup import get_logger
from ..htmlparse import extract_main_text, fetch_soup, find_attachment_link
from ..http import DEFAULT_HEADERS

logger = get_logger("sources.csrc")

__all__ = [
    "extract_text_from_html",
    "fetch_html_page",
    "find_doc_link_in_page",
    "find_pdf_link_in_page",
]

#: 列表页 / 侧边栏常见路径前缀，全页面兜底查找时需要排除
SIDEBAR_PREFIXES: tuple[str, ...] = (
    "hydj",
    "xwfb",
    "hdjl",
    "hyyj",
    "sjtj",
    "zwgk",
    "tz",
)


def fetch_html_page(html_url: str) -> BeautifulSoup | None:
    """获取详情页并解析；失败返回 ``None``。"""
    return fetch_soup(html_url, headers=DEFAULT_HEADERS)


def find_pdf_link_in_page(page_url: str, soup: BeautifulSoup | None = None) -> str | None:
    """查找详情页内的 PDF 附件链接。"""
    if soup is None:
        soup = fetch_html_page(page_url)
        if soup is None:
            return None
    return find_attachment_link(
        page_url, soup, suffixes=(".pdf",), sidebar_prefixes=SIDEBAR_PREFIXES
    )


def find_doc_link_in_page(page_url: str, soup: BeautifulSoup | None = None) -> str | None:
    """查找详情页内的 Word 附件链接（内蒙古等局以 .docx 提供决定书）。"""
    if soup is None:
        soup = fetch_html_page(page_url)
        if soup is None:
            return None
    return find_attachment_link(
        page_url, soup, suffixes=(".doc", ".docx"), sidebar_prefixes=SIDEBAR_PREFIXES
    )


def extract_text_from_html(
    html_url: str, soup: BeautifulSoup | None = None, min_length: int = 50
) -> str | None:
    """从 CSRC 详情页提取正文。

    CSRC 正文容器为 ``div.detail-news``，内部用大量 ``<span><font>`` 包裹文字片段，
    因此必须 ``unwrap`` 行内标签后再按块级元素分段（``unwrap_inline=True``）。
    """
    if soup is None:
        soup = fetch_html_page(html_url)
        if soup is None:
            return None
    return extract_main_text(soup, unwrap_inline=True, min_length=min_length)
