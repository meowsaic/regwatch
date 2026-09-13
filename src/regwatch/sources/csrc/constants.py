"""CSRC 采集的常量配置。"""

from __future__ import annotations

from datetime import datetime

__all__ = [
    "CASE_TYPE_CN",
    "CASE_TYPE_MEASURE",
    "CASE_TYPE_PENALTY",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_START_DATE",
    "DELAY_BETWEEN_CASES",
    "DELAY_BETWEEN_PAGES",
    "LIST_PAGE_RETRIES",
    "MAX_CONCURRENCY",
    "MAX_CONSECUTIVE_PAGE_FAILURES",
    "MIN_CONCURRENCY",
    "SITE_ROOT",
]

#: 行政处罚
CASE_TYPE_PENALTY = "penalty"
#: 行政监管措施
CASE_TYPE_MEASURE = "measure"

CASE_TYPE_CN: dict[str, str] = {
    CASE_TYPE_PENALTY: "行政处罚",
    CASE_TYPE_MEASURE: "监管措施",
}

#: 默认抓取起始日期（覆盖 2022 年以来；仅空库首次抓取时使用）
DEFAULT_START_DATE = datetime(2022, 1, 1).date()

#: 站点根地址，用于拼接相对链接
SITE_ROOT = "https://www.csrc.gov.cn/"

#: 列表页翻页间隔（秒）
DELAY_BETWEEN_PAGES = 1.5
#: 串行模式下详情页请求之间的间隔（秒）
DELAY_BETWEEN_CASES = 1.0
#: 单个列表页请求失败重试次数
LIST_PAGE_RETRIES = 3
#: 连续多页失败上限
MAX_CONSECUTIVE_PAGE_FAILURES = 3

#: 并发抓取：默认 / 最小 / 最大线程数
DEFAULT_CONCURRENCY = 8
MIN_CONCURRENCY = 1
MAX_CONCURRENCY = 32
