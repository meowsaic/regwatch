"""证监会各派出机构（证监局）来源配置。

共 37 个来源 = 会本部（``HQ``）+ 36 家派出机构。每个 :class:`Bureau` 包含：

- ``name_cn`` / ``name_en``：中文名称与英文标识
- ``path_code``：官网路径编码
- ``measure_url``：监管措施列表页 URL
- ``penalty_url`` / ``penalty_channelid``：行政处罚列表页 URL 与频道号
- ``site_path``：地方局站点路径（会本部为 ``csrc``）

除配置外还提供：

- :func:`discover_penalty_url`：在官网首页自动发现「行政处罚」栏目
- :func:`get_bureau_by_name` / :func:`get_bureau_by_code`：按英文标识或路径编码查找

URL 模式说明：

- 监管措施列表页：``https://www.csrc.gov.cn/csrc/c{path_code}/common_list_gd.shtml``
- 行政处罚列表页（会本部）：``https://www.csrc.gov.cn/csrc/c101928/common_list.shtml?channelid=...``
- 行政处罚列表页（地方局）：``https://www.csrc.gov.cn/{site_path}/c{xxx}/zfxxgk_zdgk.shtml?channelid=...``
- 行政处罚栏目：37 个来源均已离线配置（见模块内 ``_PENALTY_CHANNELS``），
  抓取时不再逐局访问首页；:func:`discover_penalty_url` 仅作为配置缺失 / 栏目更换时的兜底。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from ..logging_setup import get_logger
from .htmlparse import attr_str
from .http import DEFAULT_HEADERS, DEFAULT_TIMEOUT

logger = get_logger("sources.bureaus")


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
        """初始化后处理：补全行政处罚栏目配置与 site_path 默认值。"""
        # 行政处罚栏目：显式传参优先；未传时从集中配置 _PENALTY_CHANNELS 读取
        if not self.penalty_url:
            channel = _PENALTY_CHANNELS.get(self.name_en)
            if channel:
                base_url, channel_id = channel
                self.penalty_url = f"{base_url}?channelid={channel_id}"
                self.penalty_channelid = channel_id
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


# ── 行政处罚栏目配置（37 个来源，2026-09-13 离线发现并固化） ──
# 显式配置后，抓取时不再访问地方局首页做自动发现——首页偶发 403 / 限流
# 曾导致甘肃、贵州等局的行政处罚整块抓不到（无 UA 请求必现 403）。
# 个别来源的特殊说明：
#   * HQ：栏目页为 common_list.shtml，searchList API 返回 channelName="行政处罚"，total≈2000；
#   * InnerMongolia：首页导航「行政处罚」指向的栏目无法列出案例，正确栏目在 c103722。
# 网站改版导致某个栏目失效时，删除对应条目即可回落到 discover_penalty_url 自动发现。
_PENALTY_CHANNELS: dict[str, tuple[str, str]] = {
    "HQ": (
        "https://www.csrc.gov.cn/csrc/c101928/common_list.shtml",
        "28de6b87eda140cb93de4dd10d11867d",
    ),
    "Beijing": (
        "https://www.csrc.gov.cn/beijing/c103570/zfxxgk_zdgk.shtml",
        "53be9cf2273744cda5c5780e0dded972",
    ),
    "Tianjin": (
        "https://www.csrc.gov.cn/tianjin/c103608/zfxxgk_zdgk.shtml",
        "8dce98784326429e9c97515b634723fe",
    ),
    "Hebei": (
        "https://www.csrc.gov.cn/hebei/c103646/zfxxgk_zdgk.shtml",
        "e838879760e84c668062433e2cdbc389",
    ),
    "Shanxi": (
        "https://www.csrc.gov.cn/shanxi/c103684/zfxxgk_zdgk.shtml",
        "085ed9d1e1dc437cb9945e961fb2f5a0",
    ),
    "InnerMongolia": (
        "https://www.csrc.gov.cn/neimenggu/c103722/zfxxgk_zdgk.shtml",
        "e4f2dbcdd85c4bac9a9e9a6f2d093d19",
    ),
    "Liaoning": (
        "https://www.csrc.gov.cn/liaoning/c103760/zfxxgk_zdgk.shtml",
        "94cc260b27d2430b8c39c21e4627cf2e",
    ),
    "Jilin": (
        "https://www.csrc.gov.cn/jilin/c103798/zfxxgk_zdgk.shtml",
        "9a3520fd5ea644c2ad6e05acd0a05401",
    ),
    "Heilongjiang": (
        "https://www.csrc.gov.cn/heilongjiang/c103836/zfxxgk_zdgk.shtml",
        "370916bda2524acdb187191fbba13f44",
    ),
    "Shanghai": (
        "https://www.csrc.gov.cn/shanghai/c103874/zfxxgk_zdgk.shtml",
        "c8318fc200764e38b30116c2d5f72b4b",
    ),
    "Jiangsu": (
        "https://www.csrc.gov.cn/jiangsu/c103912/zfxxgk_zdgk.shtml",
        "b335834574a34e43874ef3dba68d5be5",
    ),
    "Zhejiang": (
        "https://www.csrc.gov.cn/zhejiang/c103950/zfxxgk_zdgk.shtml",
        "441d2ae10ef240cbbcfca758a73356f2",
    ),
    "Anhui": (
        "https://www.csrc.gov.cn/anhui/c103988/zfxxgk_zdgk.shtml",
        "83fb5344bde849ea8973f3715a680a15",
    ),
    "Fujian": (
        "https://www.csrc.gov.cn/fujian/c104064/zfxxgk_zdgk.shtml",
        "3df69151384a46cf8c8d11584cff5e94",
    ),
    "Jiangxi": (
        "https://www.csrc.gov.cn/jiangxi/c104178/zfxxgk_zdgk.shtml",
        "97691c60bc9a4b96af8b79df53b13b74",
    ),
    "Shandong": (
        "https://www.csrc.gov.cn/shandong/c104216/zfxxgk_zdgk.shtml",
        "3a1acab4996548faa7532b957ebf4f8e",
    ),
    "Henan": (
        "https://www.csrc.gov.cn/henan/c104292/zfxxgk_zdgk.shtml",
        "9724ed53f22a4333b91c65c349edaf48",
    ),
    "Hubei": (
        "https://www.csrc.gov.cn/hubei/c104406/zfxxgk_zdgk.shtml",
        "f0a7388893fa4c15a2d6482510e2fb31",
    ),
    "Hunan": (
        "https://www.csrc.gov.cn/hunan/c104482/zfxxgk_zdgk.shtml",
        "9182876b7fe841d3a2990998f5b756f3",
    ),
    "Guangdong": (
        "https://www.csrc.gov.cn/guangdong/c104558/zfxxgk_zdgk.shtml",
        "02a93424320e46dea2631da827f96174",
    ),
    "Guangxi": (
        "https://www.csrc.gov.cn/guangxi/c104672/zfxxgk_zdgk.shtml",
        "f12807ebb0f948afaf7070627fadad7e",
    ),
    "Hainan": (
        "https://www.csrc.gov.cn/hainan/c104748/zfxxgk_zdgk.shtml",
        "6d27bb42929c46ae8487a14f41fab43b",
    ),
    "Chongqing": (
        "https://www.csrc.gov.cn/chongqing/c104824/zfxxgk_zdgk.shtml",
        "febe5cf9074b4ce6a52fd3d34d7a5cba",
    ),
    "Sichuan": (
        "https://www.csrc.gov.cn/sichuan/c104900/zfxxgk_zdgk.shtml",
        "6cd6f0ccbd2f49c6af32878d34c54ae6",
    ),
    "Guizhou": (
        "https://www.csrc.gov.cn/guizhou/c104862/zfxxgk_zdgk.shtml",
        "8bcfadbce6b74f178c37e6eafa9438b0",
    ),
    "Yunnan": (
        "https://www.csrc.gov.cn/yunnan/c104786/zfxxgk_zdgk.shtml",
        "99481e931fc94d13b8592449acb386a4",
    ),
    "Tibet": (
        "https://www.csrc.gov.cn/xizang/c104710/zfxxgk_zdgk.shtml",
        "10f9fd2824c444ffafeaed5192682d55",
    ),
    "Shaanxi": (
        "https://www.csrc.gov.cn/shaanxi/c104634/zfxxgk_zdgk.shtml",
        "f0dad1ecc157416ea412e4a0608e04bd",
    ),
    "Gansu": (
        "https://www.csrc.gov.cn/gansu/c104596/zfxxgk_zdgk.shtml",
        "8e13b6a296324a60bc247a4a8a1df6a6",
    ),
    "Qinghai": (
        "https://www.csrc.gov.cn/qinghai/c104520/zfxxgk_zdgk.shtml",
        "439a663f89484376be17d4dcae953254",
    ),
    "Ningxia": (
        "https://www.csrc.gov.cn/ningxia/c104444/zfxxgk_zdgk.shtml",
        "45da89683b9d4a4c9657ebc12da2a73c",
    ),
    "Xinjiang": (
        "https://www.csrc.gov.cn/xinjiang/c104368/zfxxgk_zdgk.shtml",
        "3d0077b1d74e4965ade6bdf997d3fce3",
    ),
    "Shenzhen": (
        "https://www.csrc.gov.cn/shenzhen/c104330/zfxxgk_zdgk.shtml",
        "a3d847e5be4d40a5baaf387be4a56e9b",
    ),
    "Dalian": (
        "https://www.csrc.gov.cn/dalian/c104254/zfxxgk_zdgk.shtml",
        "fd0d6ccfc90640ec8af49b99d4af14f2",
    ),
    "Ningbo": (
        "https://www.csrc.gov.cn/ningbo/c104140/zfxxgk_zdgk.shtml",
        "8e2d5a7bf989489fb9caa99a39bfaa70",
    ),
    "Xiamen": (
        "https://www.csrc.gov.cn/xiamen/c104102/zfxxgk_zdgk.shtml",
        "0c1b68108fca4de0b240caaa32f901ed",
    ),
    "Qingdao": (
        "https://www.csrc.gov.cn/qingdao/c104026/zfxxgk_zdgk.shtml",
        "e3e00f2b72414f1c9ea951eeb010a6cf",
    ),
}


# 所有 37 个证监局的完整配置清单
BUREAUS: list[Bureau] = [
    Bureau(
        name_cn="证监会",
        name_en="HQ",
        path_code="c106259",
        measure_url=_build_measure_url("c106259"),
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


def get_bureau_by_name(name_en: str) -> Bureau | None:
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


def get_bureau_by_code(path_code: str) -> Bureau | None:
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


def discover_penalty_url(bureau: Bureau) -> str | None:
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
        logger.info("局 %s(%s) 已配置行政处罚 URL，直接返回。", bureau.name_cn, bureau.name_en)
        return bureau.penalty_url

    # 地方局需要 site_path 才能构造首页 URL
    if not bureau.site_path or bureau.site_path == "csrc":
        logger.warning(
            "局 %s(%s) 缺少 site_path，无法推断首页 URL。", bureau.name_cn, bureau.name_en
        )
        return None

    target_url = f"https://www.csrc.gov.cn/{bureau.site_path}/index.shtml"
    logger.info(
        "局 %s(%s) 开始发现行政处罚 URL，访问首页：%s", bureau.name_cn, bureau.name_en, target_url
    )

    # 首页偶发 403 / 超时（限流）会让整个局的 penalty 抓取落空，因此重试一次。
    response: requests.Response | None = None
    for attempt in range(2):
        try:
            response = requests.get(
                target_url,
                headers=DEFAULT_HEADERS,
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "utf-8"
            break
        except requests.RequestException as exc:
            response = None
            logger.warning(
                "局 %s(%s) 请求首页失败（第 %d 次）：%s",
                bureau.name_cn,
                bureau.name_en,
                attempt + 1,
                exc,
            )
            if attempt == 0:
                time.sleep(2.0)
    if response is None:
        logger.error("局 %s(%s) 请求首页连续失败，放弃发现", bureau.name_cn, bureau.name_en)
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
        return "channelid=" in href

    # 第一优先级：text 完全等于"行政处罚"的栏目链接
    for anchor in soup.find_all("a"):
        text = anchor.get_text(strip=True)
        href = attr_str(anchor, "href")
        if text != "行政处罚":
            continue
        if not _is_local_channel_link(href):
            continue
        full_url = _resolve_url(href, target_url)
        logger.info(
            "局 %s(%s) 发现行政处罚栏目链接（精确匹配）：%s -> %s",
            bureau.name_cn,
            bureau.name_en,
            text,
            full_url,
        )
        return full_url

    # 退化：text 含"行政处罚"的栏目链接（排除详情页）
    for anchor in soup.find_all("a"):
        text = anchor.get_text(strip=True)
        href = attr_str(anchor, "href")
        if "行政处罚" not in text:
            continue
        if not _is_local_channel_link(href):
            continue
        full_url = _resolve_url(href, target_url)
        logger.info(
            "局 %s(%s) 发现行政处罚栏目链接（模糊匹配）：%s -> %s",
            bureau.name_cn,
            bureau.name_en,
            text,
            full_url,
        )
        return full_url

    logger.warning("局 %s(%s) 未在首页发现地方局行政处罚栏目链接。", bureau.name_cn, bureau.name_en)
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
