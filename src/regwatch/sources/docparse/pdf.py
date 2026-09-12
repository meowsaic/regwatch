"""PDF 附件正文提取（基于 pypdf）。"""

from __future__ import annotations

import re

from ...logging_setup import get_logger
from ..http import shared_session
from ..progress import progress

logger = get_logger("sources.docparse")


def _extract_text_from_pdf_bytes(content: bytes) -> str:
    """从 PDF 字节内容提取文本（非 OCR，仅文本型 PDF 有效）。"""
    import io

    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("  [PDF] pypdf 未安装，无法提取文本")
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception as e:
        logger.warning(f"  [PDF] 提取失败: {e}")
        return ""


def extract_text_from_pdf(pdf_url: str) -> str | None:
    """下载 PDF 并提取文本（非 OCR，仅文本型 PDF 有效）。

    CSRC 部分行政处罚/监管措施以 PDF 附件形式提供，正文为可提取的
    文本型 PDF（非扫描件）。本函数用 pypdf 提取文本；若为扫描型 PDF
    则返回 None（不做 OCR）。

    Args:
        pdf_url: PDF 文件的 URL。

    Returns:
        提取的纯文本；失败或扫描型 PDF 返回 None。
    """
    try:
        session = shared_session()
        resp = session.get(pdf_url)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        logger.warning(f"  [PDF] 下载失败 {pdf_url}: {e}")
        return None

    text = _extract_text_from_pdf_bytes(content)
    if not text or len(text) <= 50:
        logger.warning(f"  [PDF] 提取文本过短（{len(text or '')} 字符），可能是扫描型 PDF")
        return None

    text = re.sub(r"\n{3,}", "\n\n", text)
    progress(f"  [PDF] 提取成功，{len(text)} 字符")
    return text
