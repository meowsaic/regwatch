"""CSRC 单案例抓取：详情页 → 正文 → :class:`CaseData`。

只负责「取回正文并判定基金相关性」，**不负责落盘**——持久化由
:mod:`regwatch.sources.csrc.runner` 统一交给案例仓储完成。
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urljoin

from ...logging_setup import get_logger
from ..docparse import extract_text_from_docx, extract_text_from_pdf
from ..progress import progress
from .constants import SITE_ROOT
from .htmlparse import (
    extract_text_from_html,
    fetch_html_page,
    find_doc_link_in_page,
    find_pdf_link_in_page,
)
from .models import (
    CaseData,
    build_case_id,
    extract_document_number,
    extract_punished_entities,
    is_fund_by_content,
)

logger = get_logger("sources.csrc")

__all__ = ["process_case"]


def process_case(
    link_url: str,
    title: str,
    date_str: str,
    bureau_name: str,
    case_type: str,
    *,
    skip_fund_check: bool = False,
) -> CaseData | None:
    """处理单个案例链接：提取内容 → 基金过滤 → 文号 / 主体提取。

    Args:
        link_url: 详情页 URL（相对路径会按 :data:`SITE_ROOT` 补全）。
        title: 案例标题。
        date_str: 发布日期 ``YYYY-MM-DD``。
        bureau_name: 来源局英文标识。
        case_type: ``penalty`` / ``measure``。
        skip_fund_check: 标题已确认基金相关时跳过正文确认。

    Returns:
        :class:`CaseData`；被基金过滤或无法判定时返回 ``None``。
    """
    case_id = build_case_id(date_str, link_url)
    full_link = link_url if link_url.startswith("http") else urljoin(SITE_ROOT, link_url)

    raw_text = ""
    pdf_url = ""
    doc_url = ""

    # 1) 直接 PDF：文本型 PDF 用 pypdf，扫描型留空
    if ".pdf" in full_link.lower():
        pdf_url = full_link
        progress(f"  [PDF] 提取中: {title}")
        pdf_text = extract_text_from_pdf(full_link)
        if pdf_text and len(pdf_text) > 50:
            raw_text = pdf_text
            progress(f"  [PDF✓] {title} ({len(pdf_text)} 字符)")
        else:
            logger.warning("  [PDF提取失败] %s - 可能是扫描型 PDF 或提取失败", title)

    else:
        # 2) HTML 详情页；发现 PDF 附件仅记录链接，仍以 HTML 正文为主
        soup = fetch_html_page(full_link)
        pdf_url = find_pdf_link_in_page(full_link, soup=soup) or ""

        html_text = extract_text_from_html(full_link, soup=soup)
        if html_text and len(html_text) > 50:
            raw_text = html_text
            progress(f"  [HTML✓] {title} ({len(html_text)} 字符)")
        else:
            raw_text = html_text or ""
            doc_url = find_doc_link_in_page(full_link, soup=soup) or ""
            if doc_url:
                doc_text = extract_text_from_docx(doc_url)
                if doc_text and len(doc_text) > 50:
                    raw_text = doc_text
                    progress(f"  [Word✓] {title} ({len(doc_text)} 字符)")
                else:
                    logger.warning("  [Word提取失败] %s - docx 文本提取失败", title)
            elif pdf_url:
                pdf_text = extract_text_from_pdf(pdf_url)
                if pdf_text and len(pdf_text) > 50:
                    raw_text = pdf_text
                    progress(f"  [PDF✓] {title} ({len(pdf_text)} 字符)")
                else:
                    logger.warning("  [无内容] %s - HTML无正文且 PDF 提取失败", title)
            else:
                logger.warning("  [无内容] %s - HTML无有效正文且无 Word/PDF 附件链接", title)

    # 3) 基金相关性确认（标题模糊时依据正文）
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
        progress(f"  [过滤] 无正文且标题未确认基金: {title}")
        return None

    case = CaseData(
        case_id=case_id,
        source_url=full_link,
        case_type=case_type,
        bureau=bureau_name,
        title=title,
        date=date_str,
        raw_text=raw_text,
        fetch_time=datetime.now().isoformat(),
        error="" if len(raw_text) > 50 else "未能提取到有效文本",
        is_fund_related=is_fund,
        fund_evidence=fund_evidence,
        document_number=extract_document_number(raw_text),
        punished_entities=extract_punished_entities(title, raw_text),
        pdf_url=pdf_url,
        doc_url=doc_url,
    )

    progress(f"  [{'✓' if case.has_text else '✗'}] {case_id} | {len(case.raw_text)} 字符")
    return case
