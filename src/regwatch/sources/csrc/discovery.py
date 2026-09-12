"""CSRC 列表页 / 搜索接口的链接发现。"""

from __future__ import annotations

import re
import time
from datetime import date
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ...logging_setup import get_logger
from ..bureaus import Bureau, discover_penalty_url
from ..common import parse_date_from_text
from ..htmlparse import as_tag, attr_str
from ..http import shared_session
from ..progress import progress
from .constants import (
    DELAY_BETWEEN_PAGES,
    LIST_PAGE_RETRIES,
    MAX_CONSECUTIVE_PAGE_FAILURES,
)
from .models import is_non_case_title

logger = get_logger("sources.csrc")

#: 页面框架区域标签（导航 / 页眉页脚）里的链接不是案例
_CHROME_TAGS = frozenset({"nav", "header", "footer"})

#: 页面框架区域 class 关键词（顶部工具条 / 侧栏 / 面包屑 / 搜索框等）
_CHROME_CLASSES = frozenset(
    {
        "banner",
        "breadcrumb",
        "crumb",
        "footer",
        "header",
        "menu",
        "nav",
        "nav-bar",
        "navbar",
        "search",
        "sidebar",
        "tips",
        "top-bar",
    }
)

#: 列表页 / 索引页 / 栏目页 URL（绝不是案例详情页）
_NON_DETAIL_URL = re.compile(
    r"(?:^|/)(?:index|list|common_list(?:_gd)?|zfxxgk_zdgk)(?:_\d+)?\.s?html?$",
    re.IGNORECASE,
)


def _is_page_chrome(tag: Tag) -> bool:
    """链接是否位于导航 / 页眉页脚 / 侧栏等页面框架区域。

    列表页解析在找不到专用容器时会退化为 ``ul li`` 全页匹配，
    必须排除页面框架，否则导航栏与局列表等链接会混入案例列表。
    """
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        if (parent.name or "").lower() in _CHROME_TAGS:
            return True
        classes = {str(item).lower() for item in (parent.get("class") or [])}
        if classes & _CHROME_CLASSES:
            return True
    return False


def _is_non_detail_url(url: str) -> bool:
    """URL 是否指向列表页 / 索引页 / 栏目页而非案例详情页。"""
    return bool(_NON_DETAIL_URL.search(urlsplit(url).path))


def _is_case_link(link_tag: Tag, url: str) -> bool:
    """列表条目是否是可抓取的案例详情页链接。"""
    return bool(url) and not _is_page_chrome(link_tag) and not _is_non_detail_url(url)


# ──────────────────────────── 列表页爬取 ────────────────────────────


def _parse_list_items_from_soup(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """从 CSRC 列表页 HTML 中解析案例条目。

    监管措施列表项格式：`<li><a href="...content.shtml">标题</a>日期</li>`
    行政处罚列表项格式（表格）：`<tr><td>序号</td><td><a href>标题</a></td>...</td><td>日期</td></tr>`

    Returns:
        [{link_url, title, date}, ...]  date 为 ``datetime.date``
    """
    items: list[Tag] = []

    # 1) 优先尝试 li 列表结构（监管措施页常见）
    for sel in [
        "ul.list li",
        "ul.news_list li",
        ".list-main li",
        "div.list ul li",
        "ul li",
        ".content li",
    ]:
        found = soup.select(sel)
        if found:
            items = found
            break

    results: list[dict] = []

    if items:
        for item in items:
            link_tag = as_tag(item.find("a", href=True))
            if link_tag is None:
                continue
            raw_title = link_tag.get_text(strip=True)
            if not raw_title or len(raw_title) < 4 or is_non_case_title(raw_title):
                continue
            link_url = attr_str(link_tag, "href")
            if not _is_case_link(link_tag, link_url):
                continue
            full_row_text = item.get_text(separator=" ", strip=True)
            item_date = parse_date_from_text(full_row_text)
            if not item_date:
                continue
            results.append(
                {
                    "link_url": link_url,
                    "title": raw_title,
                    "date": item_date,
                }
            )

    # 2) 表格结构（部分行政处罚页可能采用）
    if not results:
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            link_tag = as_tag(row.find("a", href=True))
            if link_tag is None:
                continue
            raw_title = link_tag.get_text(strip=True)
            if not raw_title or len(raw_title) < 4 or is_non_case_title(raw_title):
                continue
            link_url = attr_str(link_tag, "href")
            if not _is_case_link(link_tag, link_url):
                continue
            row_text = row.get_text(separator=" ", strip=True)
            item_date = parse_date_from_text(row_text)
            if not item_date:
                continue
            results.append(
                {
                    "link_url": link_url,
                    "title": raw_title,
                    "date": item_date,
                }
            )

    # 规范化相对 URL
    for r in results:
        link_url = r["link_url"]
        if isinstance(link_url, str) and not link_url.startswith("http"):
            r["link_url"] = urljoin(base_url, link_url)

    return results


def _filter_links_by_date(
    links: list[dict],
    start_date: date,
    end_date: date,
) -> tuple[list[dict], bool]:
    """按日期过滤列表条目，返回 (保留条目, 是否应停止翻页)。

    列表按日期倒序排列：
        - 日期 > end_date：跳过
        - 日期 < start_date：跳过并触发停止翻页
    """
    keep: list[dict] = []
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
    start_date: date,
    end_date: date,
) -> list[dict]:
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

    session = shared_session()
    results: list[dict] = []
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
                progress(
                    f"  [{bureau.name_en}] 翻页: 第 {page_index} 页, 累计 {len(results)} 个案例"
                )
                if stop:
                    progress(f"  [{bureau.name_en}] 遇到早于 {start_date} 的条目，停止翻页")
                    should_stop = True
                page_ok = True
                break

            except Exception as e:
                if retry < LIST_PAGE_RETRIES - 1:
                    wait = 3 * (retry + 1)
                    logger.warning(
                        f"  列表页请求失败 (重试 {retry + 1}/{LIST_PAGE_RETRIES}): {e}，{wait}秒后重试..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"  列表页处理出错 (已重试{LIST_PAGE_RETRIES}次): {e}")

        if should_stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(
                f"  第 {page_index} 页跳过 (连续失败 {consecutive_failures}/{MAX_CONSECUTIVE_PAGE_FAILURES})"
            )
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
    start_date: date,
    end_date: date,
    max_pages: int = 50,
    penalty_url: str = "",
    channelid: str = "",
) -> list[dict] | None:
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

    session = shared_session()
    # 注意：CSRC 服务器会忽略 _pageSize 参数，实际每页固定返回 20 条。
    # 因此不能用 page * page_size >= total 判断是否取完，必须用实际返回条数。
    requested_page_size = 50
    results: list[dict] = []
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
                if is_non_case_title(str(title)) or _is_non_detail_url(str(url)):
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


def _try_penalty_api(
    bureau: Bureau, start_date: date, end_date: date, penalty_url: str = ""
) -> list[dict] | None:
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

    session = shared_session()
    candidates: list[str] = []

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
                    resp = session.get(
                        api_url, params=params, headers={"X-Requested-With": "XMLHttpRequest"}
                    )
                else:
                    resp = session.post(
                        api_url, json=params, headers={"X-Requested-With": "XMLHttpRequest"}
                    )
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


def _parse_penalty_api_response(data: dict, start_date: date, end_date: date) -> list[dict]:
    """解析行政处罚 API 返回的 JSON，提取案例条目。"""
    results: list[dict] = []
    # 兼容多种字段命名
    items = data.get("content") or data.get("list") or data.get("data") or data.get("rows") or []
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
        if is_non_case_title(str(title)) or _is_non_detail_url(str(url)):
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
    start_date: date,
    end_date: date,
) -> list[dict]:
    """爬取行政处罚列表页。

    优先尝试 HTML 解析；若 HTML 无列表数据（AJAX 动态加载），探测后端 API；
    若以上均失败，记录日志并跳过（不使用浏览器自动化，保持简单）。
    """
    # 必要时通过 discover_penalty_url 自动发现
    penalty_url = bureau.penalty_url
    if not penalty_url:
        try:
            penalty_url = discover_penalty_url(bureau) or ""
        except Exception as e:
            logger.warning(f"[{bureau.name_en}] 行政处罚 URL 自动发现失败: {e}")
    if not penalty_url:
        logger.warning(f"[{bureau.name_en}/penalty] 未获取到行政处罚 URL，跳过")
        return []

    # 提取 channelid，供后续 searchList API 使用
    discovered_channelid = bureau.penalty_channelid or _extract_channelid_from_url(penalty_url)

    # 行政处罚分页 URL 模板：zfxxgk_zdgk_{N}.shtml（与监管措施同理）
    page_url_tpl = re.sub(r"zfxxgk_zdgk\.shtml", "zfxxgk_zdgk_{N}.shtml", penalty_url)

    session = shared_session()
    results: list[dict] = []
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
                    progress(
                        f"  [{bureau.name_en}] 翻页: 第 {page_index} 页, 累计 {len(results)} 个案例"
                    )
                    if stop:
                        should_stop = True
                    page_ok = True
                    break

                # HTML 无列表项：可能是 AJAX 加载，仅在第一页探测 API
                if page_index == 1:
                    progress(f"  [{bureau.name_en}] HTML 无列表项，尝试 searchList API...")
                    # 优先使用 searchList API（CSRC 官网 render.js 调用的接口）
                    api_items = _try_searchlist_api(
                        bureau,
                        start_date,
                        end_date,
                        penalty_url=penalty_url,
                        channelid=discovered_channelid,
                    )
                    if not api_items:
                        # 退化到旧的 list.do 探测
                        progress(
                            f"  [{bureau.name_en}] searchList API 无结果，尝试 list.do 探测..."
                        )
                        api_items = _try_penalty_api(
                            bureau,
                            start_date,
                            end_date,
                            penalty_url=penalty_url,
                        )
                    if api_items:
                        results.extend(api_items)
                        progress(f"  [{bureau.name_en}] [API] 共获取 {len(api_items)} 个案例")
                        page_ok = True
                        should_stop = True  # API 通常一次性返回，不再翻页
                        break
                    else:
                        logger.warning("  行政处罚 API 探测失败，停止该来源抓取")
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
                    logger.warning(
                        f"  列表页请求失败 (重试 {retry + 1}/{LIST_PAGE_RETRIES}): {e}，{wait}秒后重试..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"  列表页处理出错 (已重试{LIST_PAGE_RETRIES}次): {e}")

        if should_stop:
            break

        if not page_ok:
            consecutive_failures += 1
            logger.warning(
                f"  第 {page_index} 页跳过 (连续失败 {consecutive_failures}/{MAX_CONSECUTIVE_PAGE_FAILURES})"
            )
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
