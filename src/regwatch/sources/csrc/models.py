"""CSRC 采集的数据模型与纯文本解析函数。

只含数据结构与不依赖网络的解析逻辑（URL → 案例 ID、标题 → 受处罚主体、
正文 → 文号、基金相关性粗判），便于单独单测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...domain import CaseRecord, CaseType, Dataset
from ..common import extract_csrc_case_id

__all__ = [
    "NON_CASE_TITLE_KEYWORDS",
    "NON_FUND_TITLE_KEYWORDS",
    "CaseData",
    "build_case_id",
    "extract_document_number",
    "extract_full_org_name_from_text",
    "extract_org_name_from_title",
    "extract_punished_entities",
    "is_fund_by_content",
    "is_fund_by_title",
    "is_non_case_title",
]


@dataclass
class CaseData:
    """单个案例的完整数据模型。

    字段对齐 CSRC 详情页实际内容：元数据表格包含索引号、分类、发布机构、
    发文日期、名称、文号、主题词。
    """

    case_id: str  # 唯一标识，如 20260213_c7615688
    source_url: str  # 详情页 URL
    case_type: str  # penalty | measure
    bureau: str  # 来源局英文标识，如 HQ / Beijing
    title: str  # 案例标题
    date: str  # 发布日期 YYYY-MM-DD
    raw_text: str = ""  # 清洗后的正文全文
    fetch_time: str = ""
    error: str = ""
    is_fund_related: bool = False  # 是否基金相关
    fund_evidence: str = ""  # 基金判定依据
    document_number: str = ""  # 文号，如"〔2025〕47号"
    punished_entities: str = ""  # 受处罚主体（多个用顿号分隔）
    pdf_url: str = ""  # PDF 附件 URL
    doc_url: str = ""  # Word 文档附件 URL

    @property
    def has_text(self) -> bool:
        return len(self.raw_text or "") > 50

    def to_case_record(self) -> CaseRecord:
        """转为统一的领域记录以便入库。"""
        return CaseRecord(
            dataset=Dataset.CSRC,
            case_id=self.case_id,
            source_url=self.source_url,
            title=self.title,
            date=self.date,
            fetch_time=self.fetch_time,
            error=self.error,
            pdf_url=self.pdf_url,
            case_type=self.case_type,
            bureau=self.bureau,
            document_number=self.document_number,
            punished_entities=self.punished_entities,
            is_fund_related=self.is_fund_related,
            fund_evidence=self.fund_evidence,
            doc_url=self.doc_url,
            raw_text=self.raw_text,
        )


def build_case_id(date_str: str, url: str) -> str:
    """生成案例 ID：日期前缀 + URL 内容编号，如 ``20260213_c7615688``。"""
    content_id = extract_csrc_case_id(url)
    date_compact = re.sub(r"\D", "", date_str or "")[:8]
    return f"{date_compact}_{content_id}" if date_compact else content_id


# ──────────────────────────── 受处罚主体提取 ────────────────────────────

_TITLE_SUFFIXES = (
    "的行政处罚决定书",
    "行政处罚决定书",
    "的监管措施决定书",
    "监管措施决定书",
    "的决定书",
    "决定书",
    "送达公告",
    "（送达公告）",
    "(送达公告)",
    "事先告知书",
    "复核决定书",
)

_NAME_HINT = r"(?:公司|企业|基金|合伙|中心|集团|事务所|有限|资本|投资)"
_ENTITY_HINT = r"(?:公司|企业|有限|合伙|事务所|集团)"
_PARTY_PREFIXES = ("当事人", "被申请人", "申请人", "被处罚人", "被处置机构", "被调查人")


def extract_org_name_from_title(title: str) -> str | None:
    """从案例标题中正则提取受处罚机构名称。"""
    patterns = (
        r"关于对[《]?([^》]+?)[》]?(?:的)?(?:行政处罚|监管措施|采取|撤销|注销|暂停|取消|责令|警示|监管谈话)",
        r"关于对[《]?([^》]+?)[》]?的",
        r"关于[《]?([^》]+?)[》]?(?:的)?(?:行政处罚|监管措施|决定)",
    )
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            name = match.group(1).strip()
            for suffix in _TITLE_SUFFIXES:
                name = name.replace(suffix, "")
            name = name.rstrip("的、，,")
            if len(name) >= 4:
                return name

    match = re.search(r"[（(]((?:[^()（）]|[（(][^)）]*[)）])+)[)）]", title)
    if match:
        for part in re.split(r"[、，,]", match.group(1).strip()):
            part = part.strip()
            if re.search(_NAME_HINT, part) and len(part) >= 4:
                return part

    match = re.search(r"([^\s,，、（）()]+(?:" + _NAME_HINT + r"))", title)
    if match:
        name = match.group(1).strip()
        if len(name) >= 4:
            return name
    return None


def _strip_prefix(name: str) -> str:
    for prefix in _PARTY_PREFIXES:
        for separator in ("：", ":"):
            full = f"{prefix}{separator}"
            if name.startswith(full):
                return name[len(full) :].strip()
    return name


def extract_full_org_name_from_text(short_name: str | None, raw_text: str) -> str | None:
    """从正文正则提取机构完整名称（当标题只有简称时使用）。"""
    if not raw_text or len(raw_text) < 10:
        return None

    snippet = raw_text[:3000]

    if short_name:
        match = re.search(r"([^，,：:；;\n]+)（以下简称" + re.escape(short_name) + r"）", snippet)
        if match:
            name = _strip_prefix(match.group(1).strip())
            if len(name) >= 4 and re.search(_ENTITY_HINT, name):
                return name

    for prefix in _PARTY_PREFIXES:
        match = re.search(
            prefix
            + r"[：:]\s*([^\n,，。；;（(]+?(?:"
            + _ENTITY_HINT
            + r")[^\n]*?)(?:[，,。\n（(]|$)",
            snippet,
        )
        if match and len(match.group(1).strip()) >= 4:
            return match.group(1).strip()

    for prefix in _PARTY_PREFIXES:
        match = re.search(
            prefix + r"[：:]\s*([^\n，,。；;]+?(?:有限公司|股份有限公司|企业|合伙|事务所|集团))",
            snippet,
        )
        if match and len(match.group(1).strip()) >= 4:
            return match.group(1).strip()

    match = re.search(
        r"([^，,：:；;\n]+?(?:" + _ENTITY_HINT + r")[^，,：:；;\n]*?)（以下简称", snippet
    )
    if match:
        name = _strip_prefix(match.group(1).strip())
        if len(name) >= 4:
            return name

    return None


def extract_punished_entities(title: str, raw_text: str) -> str:
    """提取受处罚主体名称（多个用顿号分隔）。"""
    name = extract_org_name_from_title(title)
    if name and re.search(_ENTITY_HINT, name):
        return name
    if raw_text:
        full_name = extract_full_org_name_from_text(name, raw_text)
        if full_name:
            return full_name
    return name or ""


# ──────────────────────────── 文号提取 ────────────────────────────


def extract_document_number(raw_text: str) -> str:
    """从正文开头提取文号，如 ``〔2025〕47号``、``沪〔2023〕31号``。"""
    if not raw_text:
        return ""
    snippet = raw_text[:500]
    for pattern in (
        r"([\u4e00-\u9fa5]{0,4})[〔［\[](\d{4})[〕］\]](\d+)号",
        r"(\d{4})年(\d+)号",
    ):
        match = re.search(pattern, snippet)
        if match:
            return match.group(0)
    return ""


# ──────────────────────────── 基金相关性粗判 ────────────────────────────

#: 标题中明确属于非基金领域的关键词
NON_FUND_TITLE_KEYWORDS: tuple[str, ...] = (
    "证券公司",
    "证券股份有限公司",
    "证券有限责任公司",
    "证券有限公司",
    "证券承销保荐",
    "期货公司",
    "期货有限公司",
    "期货股份有限公司",
    "上市公司",
    "股份有限公司（上市公司）",
    "银行",
    "商业银行",
    "股份制银行",
    "保险",
    "保险公司",
    "保险集团",
    "信托公司",
    "财务公司",
    "金融控股",
    "消费金融公司",
    "汽车金融公司",
    "证券投资咨询公司",
)


def is_fund_by_title(title: str) -> bool | None:
    """根据标题预判是否基金相关。

    Returns:
        ``True`` 标题含「基金」；``False`` 明确属于非基金领域；``None`` 需正文确认。
    """
    if not title:
        return None
    if "基金" in title:
        return True
    return False if any(keyword in title for keyword in NON_FUND_TITLE_KEYWORDS) else None


#: 标题中的新闻 / 讲话 / 会议体裁词：列表页上的这类链接不是监管处分案例
NON_CASE_TITLE_KEYWORDS: tuple[str, ...] = (
    "新闻发布",
    "致辞",
    "演讲",
    "答记者问",
    "吹风会",
    "见面会",
    "座谈会",
    "圆桌会",
    "表彰",
    "党课",
)


def is_non_case_title(title: str) -> bool:
    """标题是否属于新闻稿 / 讲话稿 / 会议报道等非处分案例体裁。

    CSRC 列表页正文区常混有「新闻发布会」「主席演讲」等栏目链接，
    链接发现阶段直接丢弃，避免被误抓成案例（历史脏数据的来源之一）。
    """
    if not title:
        return False
    return any(keyword in title for keyword in NON_CASE_TITLE_KEYWORDS)


def is_fund_by_content(raw_text: str) -> bool:
    """根据正文确认是否基金相关（粗判：正文中出现「基金」）。"""
    return bool(raw_text) and "基金" in raw_text


def case_type_of(value: str) -> CaseType:
    """把字符串解析为 :class:`~regwatch.domain.CaseType`。"""
    return CaseType.parse(value, CaseType.UNKNOWN) or CaseType.UNKNOWN
