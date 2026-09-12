"""附件正文解析：PDF 与 Office 文档。

从原先 ``csrc.py`` 中抽出的二进制解析逻辑，供 AMAC 与 CSRC 两个采集器共用。
"""

from __future__ import annotations

from .office import _detect_doc_format, extract_text_from_docx
from .pdf import extract_text_from_pdf

__all__ = [
    "_detect_doc_format",
    "extract_text_from_docx",
    "extract_text_from_pdf",
]
