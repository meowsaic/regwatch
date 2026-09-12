"""采集层共用的 HTML 解析工具。

原先 ``_html.py`` 只有两个取值辅助函数，而「下载页面」「提取正文」「查找附件链接」
在 ``amac.py`` 与 ``csrc.py`` 里各写了一遍且细节已经漂移（侧边栏过滤前缀不同）。
这里把三者收敛为共享实现，两个采集器只传入各自的差异参数。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from .http import DEFAULT_HEADERS, HttpClient, RequestsHttpClient

__all__ = [
    "as_tag",
    "attr_str",
    "extract_main_text",
    "fetch_soup",
    "find_attachment_link",
    "parse_html",
]


def as_tag(value: Any) -> Tag | None:
    """把 BeautifulSoup 查找结果收敛为 ``Tag | None``。"""
    return value if isinstance(value, Tag) else None


def attr_str(tag: Tag | None, name: str, default: str = "") -> str:
    """读取标签属性并归一为 ``str``（多值属性取首个）。"""
    if tag is None:
        return default
    value = tag.get(name)
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else default
    return str(value)


def parse_html(text: str) -> BeautifulSoup:
    return BeautifulSoup(text, "html.parser")


def fetch_soup(
    url: str,
    *,
    http: HttpClient | None = None,
    headers: dict[str, str] | None = None,
) -> BeautifulSoup | None:
    """下载页面并解析；失败返回 ``None``。

    目标站点声明缺失 charset 时 requests 会误判为 ISO-8859-1，
    这里统一以 UTF-8 解码（两家站点均为 UTF-8）。
    """
    client = http or RequestsHttpClient()
    try:
        text = client.get_text(url, headers=headers or DEFAULT_HEADERS, encoding="utf-8")
    except Exception as exc:
        from ..logging_setup import get_logger

        get_logger("sources.html").warning("页面获取失败 %s: %s", url, exc)
        return None
    return parse_html(text)


#: 正文容器的候选选择器（按优先级）
CONTENT_SELECTORS: tuple[Any, ...] = (
    ("div", {"class": "detail-news"}),
    ("div", {"class": "TRS_Editor"}),
    ("div", {"class": "Custom_UnionStyle"}),
    ("div", {"class": re.compile(r"detail|article|content|text", re.I)}),
    ("div", {"id": re.compile(r"detail|article|content|zoom", re.I)}),
)


def extract_main_text(
    soup: BeautifulSoup,
    *,
    unwrap_inline: bool = True,
    min_length: int = 100,
) -> str | None:
    """按通用规则提取详情页正文。

    Args:
        soup: 已解析的页面。
        unwrap_inline: 是否先 ``unwrap`` 行内标签再按块级元素分段。
            证监会详情页用大量 ``<span><font>`` 包裹文字片段，必须开启，
            否则 ``get_text`` 会在每个 span 边界插入换行导致句子碎片化。
        min_length: 低于该长度视为提取失败。
    """
    area: Tag | None = None
    for name, attrs in CONTENT_SELECTORS:
        area = as_tag(soup.find(name, attrs=attrs))
        if area is not None:
            break
    if area is None:
        area = as_tag(soup.find("div", class_=re.compile(r"right|main|cont")))

    if area is None:
        text = re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n", strip=True))
        return text if len(text) > min_length else None

    for tag in area.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    work = area
    if unwrap_inline:
        from copy import copy

        work = copy(area)
        for br in work.find_all("br"):
            br.replace_with("\n")
        for name in (
            "span",
            "font",
            "a",
            "b",
            "i",
            "em",
            "strong",
            "u",
            "sub",
            "sup",
            "o:p",
            "center",
            "mark",
            "small",
            "big",
        ):
            for tag in work.find_all(name):
                tag.unwrap()
        for name in (
            "p",
            "div",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
            "table",
            "blockquote",
            "pre",
        ):
            for tag in work.find_all(name):
                tag.append("\n")
        raw = work.get_text(separator="", strip=False)
    else:
        raw = work.get_text(separator="\n", strip=True)

    lines = [line.strip() for line in raw.split("\n")]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text if len(text) > min_length else None


def find_attachment_link(
    page_url: str,
    soup: BeautifulSoup,
    *,
    suffixes: tuple[str, ...] = (".pdf",),
    sidebar_prefixes: tuple[str, ...] = (),
) -> str | None:
    """在详情页中查找附件链接（PDF / Word）。

    查找顺序：附件下载区 → 含「附件」文字的容器 → class 含附件关键词的容器
    → 全页面（过滤侧边栏路径）→ ``iframe`` / ``embed``。
    """
    lowered = tuple(suffix.lower() for suffix in suffixes)

    def matches(href: str) -> bool:
        return any(suffix in href.lower() for suffix in lowered)

    # 1) 附件下载区
    for container in soup.find_all("div", class_="attachment-down"):
        for link in container.find_all("a", href=True):
            href = attr_str(link, "href")
            if matches(href):
                return urljoin(page_url, href)

    # 2) 含「附件」文字的标题 / 段落附近
    for node in soup.find_all(string=re.compile(r"附件.*下载|下载.*附件")):
        parent = as_tag(node.find_parent("div"))
        if parent is None:
            continue
        for link in parent.find_all("a", href=True):
            href = attr_str(link, "href")
            if matches(href):
                return urljoin(page_url, href)

    # 3) class 含附件关键词的容器
    for container in soup.find_all(
        "div", class_=re.compile(r"fujian|attachment|download|accessory", re.I)
    ):
        for link in container.find_all("a", href=True):
            href = attr_str(link, "href")
            if matches(href):
                return urljoin(page_url, href)

    # 4) 全页面查找，过滤侧边栏链接
    if sidebar_prefixes:
        for link in soup.find_all("a", href=True):
            href = attr_str(link, "href")
            if not matches(href):
                continue
            parts = href.replace("\\", "/").split("/")
            if any(part in parts for part in sidebar_prefixes):
                continue
            return urljoin(page_url, href)
    else:
        for link in soup.find_all("a", href=True):
            href = attr_str(link, "href")
            if matches(href):
                return urljoin(page_url, href)

    # 5) iframe / embed
    for tag in list(soup.find_all("iframe", src=True)) + list(soup.find_all("embed", src=True)):
        src = attr_str(tag, "src")
        if matches(src):
            return urljoin(page_url, src)

    return None
