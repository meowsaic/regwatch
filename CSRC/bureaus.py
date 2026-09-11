"""证监会私募基金行政处罚与监管措施数据库 - 来源配置模块。

本模块定义了证监会及其派出机构（共 37 个证监局）的元数据配置，
包括各局在证监会官网中的路径编码、监管措施列表页 URL、行政处罚列表页 URL 等。

主要内容：
    - Bureau 数据类：描述单个证监局的配置信息
    - BUREAUS 列表：所有 37 个证监局的完整清单
    - discover_penalty_url 函数：自动发现各局行政处罚页 URL
    - get_bureau_by_name / get_bureau_by_code：辅助查找函数

URL 模式说明：
    - 监管措施列表页：https://www.csrc.gov.cn/csrc/c{path_code}/common_list_gd.shtml
    - 行政处罚列表页（会本部）：https://www.csrc.gov.cn/csrc/c101971/zfxxgk_zdgk.shtml?channelid=...
    - 行政处罚列表页（地方局）：https://www.csrc.gov.cn/{site_path}/c{xxx}/zfxxgk_zdgk.shtml?channelid=...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

# 模块日志器
logger = logging.getLogger(__name__)

# 请求头：模拟正常浏览器访问，避免被识别为爬虫
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 请求超时（秒）
_REQUEST_TIMEOUT = 15


@dataclass
class Bureau:
    """单个证监局的配置信息。

    Attributes:
        name_cn: 中文名称（如"证监会"、"北京证监局"）。
        name_en: 英文标识（如"HQ"、"Beijing"），用于代码中引用。
        path_code: 在证监会官网中的路径编码（如"c106259"、"c100045"）。
        measure_url: 监管措施列表页完整 URL。
        penalty_url: 行政处罚列表页完整 URL；未知则为空字符串。
        penalty_channelid: 行政处罚页面的 channelid 查询参数；无则留空。
        site_path: 地方局在 URL 中的站点路径名（如"shanghai"、"beijing"），
            会本部为"csrc"。
    """

    name_cn: str
    name_en: str
    path_code: str
    measure_url: str
    penalty_url: str = ""
    penalty_channelid: str = ""
    site_path: str = ""

    def __post_init__(self) -> None:
        """初始化后处理：若未显式指定 site_path，则根据局类型推断默认值。"""
        if self.site_path:
            return
        # 会本部站点路径为 csrc
        if self.name_en == "HQ":
            self.site_path = "csrc"
        else:
            # 地方局 site_path 由外部显式传入；缺省时给出空串，由调用方负责
            self.site_path = ""


def _build_measure_url(path_code: str) -> str:
    """根据 path_code 构造监管措施列表页 URL。

    path_code 形如 "c106259"（已含 c 前缀），直接拼接即可，
    不要再额外加 c，否则会得到 cc106259 这种错误路径。
    """
    return f"https://www.csrc.gov.cn/csrc/{path_code}/common_list_gd.shtml"


# 已知的行政处罚 channelid（来自 CSRC 官网）
# 会本部行政处罚：c101928/common_list.shtml，channelid=28de6b87eda140cb93de4dd10d11867d
# 该 channelid 通过 searchList API 返回 channelName="行政处罚"，total≈2014
_HQ_PENALTY_CHANNELID = "28de6b87eda140cb93de4dd10d11867d"
_HQ_PENALTY_URL = (
    "https://www.csrc.gov.cn/csrc/c101928/common_list.shtml"
    f"?channelid={_HQ_PENALTY_CHANNELID}"
)

# 上海局行政处罚 channelid（来自官网政务公开页配置）
_SHANGHAI_PENALTY_CHANNELID = "c8318fc200764e38b30116c2d5f72b4b"
_SHANGHAI_PENALTY_URL = (
    "https://www.csrc.gov.cn/shanghai/c103874/zfxxgk_zdgk.shtml"
    f"?channelid={_SHANGHAI_PENALTY_CHANNELID}"
)

# 内蒙古局行政处罚 channelid
# 注意：内蒙古局首页"行政处罚"导航链接指向的栏目无法正常列出案例，
# 正确的行政处罚栏目位于 c103722，需显式配置。
_NEIMENGGU_PENALTY_CHANNELID = "e4f2dbcdd85c4bac9a9e9a6f2d093d19"
_NEIMENGGU_PENALTY_URL = (
    "https://www.csrc.gov.cn/neimenggu/c103722/zfxxgk_zdgk.shtml"
    f"?channelid={_NEIMENGGU_PENALTY_CHANNELID}"
)


# 所有 37 个证监局的完整配置清单
BUREAUS: List[Bureau] = [
    Bureau(
        name_cn="证监会",
        name_en="HQ",
        path_code="c106259",
        measure_url=_build_measure_url("c106259"),
        penalty_url=_HQ_PENALTY_URL,
        penalty_channelid=_HQ_PENALTY_CHANNELID,
        site_path="csrc",
    ),
    Bureau(
        name_cn="北京证监局",
        name_en="Beijing",
        path_code="c100045",
        measure_url=_build_measure_url("c100045"),
        site_path="beijing",
    ),
    Bureau(
        name_cn="天津证监局",
        name_en="Tianjin",
        path_code="c100046",
        measure_url=_build_measure_url("c100046"),
        site_path="tianjin",
    ),
    Bureau(
        name_cn="河北证监局",
        name_en="Hebei",
        path_code="c100047",
        measure_url=_build_measure_url("c100047"),
        site_path="hebei",
    ),
    Bureau(
        name_cn="山西证监局",
        name_en="Shanxi",
        path_code="c100048",
        measure_url=_build_measure_url("c100048"),
        site_path="shanxi",
    ),
    Bureau(
        name_cn="内蒙古证监局",
        name_en="InnerMongolia",
        path_code="c100049",
        measure_url=_build_measure_url("c100049"),
        penalty_url=_NEIMENGGU_PENALTY_URL,
        penalty_channelid=_NEIMENGGU_PENALTY_CHANNELID,
        site_path="neimenggu",
    ),
    Bureau(
        name_cn="辽宁证监局",
        name_en="Liaoning",
        path_code="c100050",
        measure_url=_build_measure_url("c100050"),
        site_path="liaoning",
    ),
    Bureau(
        name_cn="吉林证监局",
        name_en="Jilin",
        path_code="c100051",
        measure_url=_build_measure_url("c100051"),
        site_path="jilin",
    ),
    Bureau(
        name_cn="黑龙江证监局",
        name_en="Heilongjiang",
        path_code="c100052",
        measure_url=_build_measure_url("c100052"),
        site_path="heilongjiang",
    ),
    Bureau(
        name_cn="上海证监局",
        name_en="Shanghai",
        path_code="c100053",
        measure_url=_build_measure_url("c100053"),
        penalty_url=_SHANGHAI_PENALTY_URL,
        penalty_channelid=_SHANGHAI_PENALTY_CHANNELID,
        site_path="shanghai",
    ),
    Bureau(
        name_cn="江苏证监局",
        name_en="Jiangsu",
        path_code="c100054",
        measure_url=_build_measure_url("c100054"),
        site_path="jiangsu",
    ),
    Bureau(
        name_cn="浙江证监局",
        name_en="Zhejiang",
        path_code="c100055",
        measure_url=_build_measure_url("c100055"),
        site_path="zhejiang",
    ),
    Bureau(
        name_cn="安徽证监局",
        name_en="Anhui",
        path_code="c100056",
        measure_url=_build_measure_url("c100056"),
        site_path="anhui",
    ),
    Bureau(
        name_cn="福建证监局",
        name_en="Fujian",
        path_code="c100057",
        measure_url=_build_measure_url("c100057"),
        site_path="fujian",
    ),
    Bureau(
        name_cn="江西证监局",
        name_en="Jiangxi",
        path_code="c100058",
        measure_url=_build_measure_url("c100058"),
        site_path="jiangxi",
    ),
    Bureau(
        name_cn="山东证监局",
        name_en="Shandong",
        path_code="c100059",
        measure_url=_build_measure_url("c100059"),
        site_path="shandong",
    ),
    Bureau(
        name_cn="河南证监局",
        name_en="Henan",
        path_code="c100060",
        measure_url=_build_measure_url("c100060"),
        site_path="henan",
    ),
    Bureau(
        name_cn="湖北证监局",
        name_en="Hubei",
        path_code="c100061",
        measure_url=_build_measure_url("c100061"),
        site_path="hubei",
    ),
    Bureau(
        name_cn="湖南证监局",
        name_en="Hunan",
        path_code="c100062",
        measure_url=_build_measure_url("c100062"),
        site_path="hunan",
    ),
    Bureau(
        name_cn="广东证监局",
        name_en="Guangdong",
        path_code="c100063",
        measure_url=_build_measure_url("c100063"),
        site_path="guangdong",
    ),
    Bureau(
        name_cn="广西证监局",
        name_en="Guangxi",
        path_code="c100064",
        measure_url=_build_measure_url("c100064"),
        site_path="guangxi",
    ),
    Bureau(
        name_cn="海南证监局",
        name_en="Hainan",
        path_code="c100065",
        measure_url=_build_measure_url("c100065"),
        site_path="hainan",
    ),
    Bureau(
        name_cn="重庆证监局",
        name_en="Chongqing",
        path_code="c100066",
        measure_url=_build_measure_url("c100066"),
        site_path="chongqing",
    ),
    Bureau(
        name_cn="四川证监局",
        name_en="Sichuan",
        path_code="c100067",
        measure_url=_build_measure_url("c100067"),
        site_path="sichuan",
    ),
    Bureau(
        name_cn="贵州证监局",
        name_en="Guizhou",
        path_code="c100068",
        measure_url=_build_measure_url("c100068"),
        site_path="guizhou",
    ),
    Bureau(
        name_cn="云南证监局",
        name_en="Yunnan",
        path_code="c100069",
        measure_url=_build_measure_url("c100069"),
        site_path="yunnan",
    ),
    Bureau(
        name_cn="西藏证监局",
        name_en="Tibet",
        path_code="c100070",
        measure_url=_build_measure_url("c100070"),
        site_path="xizang",
    ),
    Bureau(
        name_cn="陕西证监局",
        name_en="Shaanxi",
        path_code="c100071",
        measure_url=_build_measure_url("c100071"),
        site_path="shaanxi",
    ),
    Bureau(
        name_cn="甘肃证监局",
        name_en="Gansu",
        path_code="c100072",
        measure_url=_build_measure_url("c100072"),
        site_path="gansu",
    ),
    Bureau(
        name_cn="青海证监局",
        name_en="Qinghai",
        path_code="c100073",
        measure_url=_build_measure_url("c100073"),
        site_path="qinghai",
    ),
    Bureau(
        name_cn="宁夏证监局",
        name_en="Ningxia",
        path_code="c100074",
        measure_url=_build_measure_url("c100074"),
        site_path="ningxia",
    ),
    Bureau(
        name_cn="新疆证监局",
        name_en="Xinjiang",
        path_code="c100075",
        measure_url=_build_measure_url("c100075"),
        site_path="xinjiang",
    ),
    Bureau(
        name_cn="深圳证监局",
        name_en="Shenzhen",
        path_code="c100076",
        measure_url=_build_measure_url("c100076"),
        site_path="shenzhen",
    ),
    Bureau(
        name_cn="大连证监局",
        name_en="Dalian",
        path_code="c100077",
        measure_url=_build_measure_url("c100077"),
        site_path="dalian",
    ),
    Bureau(
        name_cn="宁波证监局",
        name_en="Ningbo",
        path_code="c100078",
        measure_url=_build_measure_url("c100078"),
        site_path="ningbo",
    ),
    Bureau(
        name_cn="厦门证监局",
        name_en="Xiamen",
        path_code="c100079",
        measure_url=_build_measure_url("c100079"),
        site_path="xiamen",
    ),
    Bureau(
        name_cn="青岛证监局",
        name_en="Qingdao",
        path_code="c100080",
        measure_url=_build_measure_url("c100080"),
        site_path="qingdao",
    ),
]


def get_bureau_by_name(name_en: str) -> Optional[Bureau]:
    """根据英文名称查找证监局配置。

    Args:
        name_en: 局的英文标识，如"HQ"、"Shanghai"。

    Returns:
        匹配到的 Bureau 实例；未找到返回 None。
    """
    for bureau in BUREAUS:
        if bureau.name_en == name_en:
            return bureau
    return None


def get_bureau_by_code(path_code: str) -> Optional[Bureau]:
    """根据路径编码查找证监局配置。

    Args:
        path_code: 局的路径编码，如"c106259"、"c100045"。可带或不带"c"前缀。

    Returns:
        匹配到的 Bureau 实例；未找到返回 None。
    """
    # 规范化：保证以 c 开头，便于匹配
    normalized = path_code if path_code.startswith("c") else f"c{path_code}"
    for bureau in BUREAUS:
        if bureau.path_code == normalized:
            return bureau
    return None


def discover_penalty_url(bureau: Bureau) -> Optional[str]:
    """发现指定证监局的行政处罚列表页 URL。

    对于已知行政处罚 URL 的局（如会本部、上海局），直接返回已配置的 URL；
    对于其他地方局，访问其**首页**（index.shtml）并解析"行政处罚"链接。

    注意：地方局政务公开页（zfxxgk_zdgk.shtml）上的"行政处罚"链接指向的是
    会本部统一栏目（/csrc/c101971/...），而非该局自己的行政处罚栏目。
    地方局自己的行政处罚栏目链接位于首页导航中，URL 模式为：
        /{site_path}/c{xxx}/zfxxgk_zdgk.shtml?channelid={yyy}

    Args:
        bureau: 证监局配置实例。

    Returns:
        发现到的行政处罚 URL；未找到返回 None。
    """
    # 已知 URL 直接返回
    if bureau.penalty_url:
        logger.info("局 %s(%s) 已配置行政处罚 URL，直接返回。",
                    bureau.name_cn, bureau.name_en)
        return bureau.penalty_url

    # 地方局需要 site_path 才能构造首页 URL
    if not bureau.site_path or bureau.site_path == "csrc":
        logger.warning("局 %s(%s) 缺少 site_path，无法推断首页 URL。",
                        bureau.name_cn, bureau.name_en)
        return None

    target_url = (
        f"https://www.csrc.gov.cn/{bureau.site_path}/index.shtml"
    )
    logger.info("局 %s(%s) 开始发现行政处罚 URL，访问首页：%s",
                bureau.name_cn, bureau.name_en, target_url)

    try:
        response = requests.get(
            target_url,
            headers=_DEFAULT_HEADERS,
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
    except requests.RequestException as exc:
        logger.error("局 %s(%s) 请求首页失败：%s",
                     bureau.name_cn, bureau.name_en, exc)
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    # 在所有 <a> 标签中查找"行政处罚"栏目链接。
    #
    # 关键陷阱：首页可能同时存在
    #   1) "行政处罚罚款催告书送达公告" 这类具体案例详情页链接（content.shtml）
    #   2) "行政处罚" 栏目导航链接（zfxxgk_zdgk.shtml?channelid=...）
    # 用 "行政处罚" in text 会误匹配情况 1），导致返回错误的详情页 URL。
    # 因此采用两级匹配：
    #   首选：text 完全等于"行政处罚" 且 href 是栏目页（含 zfxxgk_zdgk + channelid）
    #   退化：text 含"行政处罚" 且 href 是栏目页（含 zfxxgk_zdgk + channelid），
    #         并排除 content.shtml 这类详情页链接
    site_prefix = f"/{bureau.site_path}/"

    def _is_local_channel_link(href: str) -> bool:
        """判断是否为地方局自己的行政处罚栏目页链接。"""
        if not href:
            return False
        # 排除指向会本部（/csrc/）的链接
        if href.startswith("/csrc/") or "/csrc/" in href:
            return False
        # 必须带 site_path 前缀（地方局自己的栏目）
        if site_prefix not in href:
            return False
        # 必须是栏目页（zfxxgk_zdgk.shtml）而非详情页（content.shtml）
        if "content.shtml" in href:
            return False
        # 栏目页通常带 channelid 查询参数
        if "channelid=" not in href:
            return False
        return True

    # 第一优先级：text 完全等于"行政处罚"的栏目链接
    for anchor in soup.find_all("a"):
        text = anchor.get_text(strip=True)
        href = anchor.get("href", "")
        if text != "行政处罚":
            continue
        if not _is_local_channel_link(href):
            continue
        full_url = _resolve_url(href, target_url)
        logger.info("局 %s(%s) 发现行政处罚栏目链接（精确匹配）：%s -> %s",
                    bureau.name_cn, bureau.name_en, text, full_url)
        return full_url

    # 退化：text 含"行政处罚"的栏目链接（排除详情页）
    for anchor in soup.find_all("a"):
        text = anchor.get_text(strip=True)
        href = anchor.get("href", "")
        if "行政处罚" not in text:
            continue
        if not _is_local_channel_link(href):
            continue
        full_url = _resolve_url(href, target_url)
        logger.info("局 %s(%s) 发现行政处罚栏目链接（模糊匹配）：%s -> %s",
                    bureau.name_cn, bureau.name_en, text, full_url)
        return full_url

    logger.warning("局 %s(%s) 未在首页发现地方局行政处罚栏目链接。",
                   bureau.name_cn, bureau.name_en)
    return None


def _resolve_url(href: str, base_url: str) -> str:
    """将相对 URL 解析为绝对 URL。

    Args:
        href: 链接的 href 属性值，可能是相对或绝对路径。
        base_url: 当前页面 URL，用于解析相对路径。

    Returns:
        完整的绝对 URL。
    """
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        # 绝对路径，拼接到站点根域名
        from urllib.parse import urlsplit
        parts = urlsplit(base_url)
        return f"{parts.scheme}://{parts.netloc}{href}"
    # 其他相对路径：简单拼接到 base_url 的目录
    base_dir = base_url.rsplit("/", 1)[0]
    return f"{base_dir}/{href}"


if __name__ == "__main__":
    # 简单自检：打印所有局清单
    logging.basicConfig(level=logging.INFO)
    print(f"共 {len(BUREAUS)} 个证监局")
    for b in BUREAUS:
        print(f"{b.name_en}: {b.name_cn} ({b.path_code}) site_path={b.site_path}")
