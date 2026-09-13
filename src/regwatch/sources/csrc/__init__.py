"""CSRC 采集子包。

按职责拆分自原先 2000 余行的 ``csrc.py``：

- :mod:`regwatch.sources.csrc.models`     数据模型与纯文本解析
- :mod:`regwatch.sources.csrc.discovery`  列表页 / 搜索接口的链接发现
- :mod:`regwatch.sources.csrc.fetcher`    单案例抓取（详情页解析 → 正文 → CaseData）
- :mod:`regwatch.sources.csrc.runner`     多来源并发抓取编排与对外 API
"""

from __future__ import annotations

from .constants import (
    CASE_TYPE_MEASURE,
    CASE_TYPE_PENALTY,
    DEFAULT_START_DATE,
)
from .models import (
    CaseData,
    build_case_id,
    extract_document_number,
    extract_punished_entities,
    is_fund_by_content,
    is_fund_by_title,
)
from .runner import (
    DEFAULT_CONCURRENCY,
    FetchResult,
    available_bureaus,
    fetch,
    fetch_pending,
    fetch_single,
)

__all__ = [
    "CASE_TYPE_MEASURE",
    "CASE_TYPE_PENALTY",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_START_DATE",
    "MAX_CONCURRENCY",
    "CaseData",
    "FetchResult",
    "available_bureaus",
    "build_case_id",
    "extract_document_number",
    "extract_punished_entities",
    "fetch",
    "fetch_pending",
    "fetch_single",
    "is_fund_by_content",
    "is_fund_by_title",
]
