"""Office 附件（.doc / .docx）正文提取。

中基协与证监会的决定书附件既有老式 OLE 复合文档（.doc），
也有 OOXML 包（.docx）；前者无官方解析器，这里做尽力而为的
WordDocument 流文本还原，并带 OLE 目录兜底。
"""

from __future__ import annotations

import re

from ...logging_setup import get_logger
from ..common import progress
from ..http import shared_session
from .pdf import _extract_text_from_pdf_bytes

logger = get_logger("sources.docparse")


def _detect_doc_format(content: bytes) -> str:
    """根据 magic bytes 检测文档实际格式。

    CSRC 附件常见三种情况：
    - OOXML (.docx)：PK 开头的 ZIP
    - OLE 复合文档 (.doc 或 .docx 误命名)：D0CF11E0 开头
    - PDF：%PDF 开头

    Args:
        content: 文件字节内容。

    Returns:
        'ole' | 'ooxml' | 'pdf' | 'unknown'
    """
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"
    if content[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "ooxml"
    if content[:5] == b"%PDF-":
        return "pdf"
    return "unknown"


def _extract_text_from_ole(content: bytes) -> str:
    """从 OLE 复合文档（.doc 旧格式）提取正文文本。

    通过 olefile 读取 WordDocument 流与 Table 流，解析 piece table
    （CLX 中的 PLCFpcd）按 CP 顺序拼接文本片段。Word Binary Format
    中文本可能为 Unicode（UTF-16-LE）或 ANSI（cp936）。

    Args:
        content: .doc 文件字节内容。

    Returns:
        提取的纯文本；失败返回空字符串。
    """
    import io

    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(content))
    except Exception as e:
        logger.warning(f"  [Word文档] OLE 打开失败: {e}")
        return ""

    try:
        if not ole.exists("WordDocument"):
            return ""
        word_stream = ole.openstream("WordDocument").read()

        # piece table 存放在 0Table 或 1Table 流
        table_name = "0Table" if ole.exists("0Table") else "1Table"
        if not ole.exists(table_name):
            return _fallback_extract_ole_text(word_stream)
        table_stream = ole.openstream(table_name).read()

        # CLX = Prc* + Pcdt；Pcdt 以 0x02 标志开头，后跟 4 字节 lcb
        # PLCFpcd = (n+1)*CP(4字节) + n*PCD(8字节)，故 lcb = 12n+4
        clx_offset = -1
        clx_len = 0
        idx = 0
        while idx < len(table_stream) - 6:
            if table_stream[idx] == 0x02:
                lcb = int.from_bytes(table_stream[idx + 1 : idx + 5], "little")
                if 0 < lcb < len(table_stream) - idx - 5 and (lcb - 4) % 12 == 0:
                    clx_offset = idx + 5
                    clx_len = lcb
                    break
            idx += 1

        if clx_offset < 0:
            return _fallback_extract_ole_text(word_stream)

        plc = table_stream[clx_offset : clx_offset + clx_len]
        n = (clx_len - 4) // 12
        cps = [int.from_bytes(plc[i * 4 : (i + 1) * 4], "little") for i in range(n + 1)]
        pcds_start = (n + 1) * 4

        text_parts: list[str] = []
        for i in range(n):
            pcd = plc[pcds_start + i * 8 : pcds_start + (i + 1) * 8]
            fc_raw = int.from_bytes(pcd[2:6], "little")
            # bit30=1 表示 ANSI 压缩编码（fc 需除以 2），bit30=0 表示 Unicode
            is_unicode = not (fc_raw & 0x40000000)
            fc = fc_raw & 0x3FFFFFFF
            char_count = cps[i + 1] - cps[i]
            if is_unicode:
                raw = word_stream[fc : fc + char_count * 2]
                text_parts.append(raw.decode("utf-16-le", errors="ignore"))
            else:
                fc = fc // 2
                raw = word_stream[fc : fc + char_count]
                text_parts.append(raw.decode("cp936", errors="ignore"))
        return "".join(text_parts)
    finally:
        ole.close()


def _fallback_extract_ole_text(stream: bytes) -> str:
    """OLE piece table 解析失败时，从 WordDocument 流直接提取文本片段。

    将整个流按 UTF-16-LE 解码后，用正则匹配连续的中英文可打印片段。
    质量不如 piece table 解析，但能兜底。
    """
    text = stream.decode("utf-16-le", errors="ignore")
    chunks = re.findall(
        r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\w\s，。；：、（）《》“”‘’！？·…—\-.\d()\[\]/%]+",
        text,
    )
    return "".join(chunks)


def _extract_text_from_ooxml(content: bytes) -> str:
    """从 OOXML (.docx) 提取文本。

    优先使用 python-docx（同时提取段落与表格）；失败时回退到直接
    解析 ZIP 内的 word/document.xml。
    """
    import io

    try:
        import docx  # python-docx

        document = docx.Document(io.BytesIO(content))
        paragraphs: list[str] = []
        for para in document.paragraphs:
            text = para.text.strip()
            if text:
                paragraphs.append(text)
        # 也提取表格中的文本（行政处罚决定书可能用表格列当事人信息）
        for table in document.tables:
            for row in table.rows:
                row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_texts:
                    paragraphs.append(" | ".join(row_texts))
        return "\n".join(paragraphs)
    except Exception as e:
        logger.warning(f"  [Word文档] python-docx 失败，回退到 XML 解析: {e}")
        return _extract_text_from_ooxml_xml(content)


def _extract_text_from_ooxml_xml(content: bytes) -> str:
    """python-docx 失败时，直接从 ZIP 中解析 word/document.xml 提取文本。"""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            doc_name = None
            for name in zf.namelist():
                if name.endswith("word/document.xml"):
                    doc_name = name
                    break
            if not doc_name:
                return ""
            with zf.open(doc_name) as f:
                tree = ET.parse(f)
        root = tree.getroot()
        w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs: list[str] = []
        for para in root.iter(f"{w_ns}p"):
            texts = [t.text for t in para.iter(f"{w_ns}t") if t.text]
            if texts:
                paragraphs.append("".join(texts))
        return "\n".join(paragraphs)
    except Exception as e:
        logger.warning(f"  [Word文档] XML 解析失败: {e}")
        return ""


def extract_text_from_docx(doc_url: str) -> str | None:
    """下载 Word 文档并提取纯文本，自动检测实际格式。

    CSRC 地方局附件常见三类情况：
    1. 标准 .docx（OOXML/ZIP）→ python-docx
    2. .doc 旧格式（OLE 复合文档）→ olefile 解析 piece table
    3. 扩展名为 .docx 但实际是 .doc（OLE）→ 同 2

    通过 magic bytes 检测实际格式后分发，避免依赖扩展名。

    Args:
        doc_url: Word 文档的 URL。

    Returns:
        提取的纯文本；失败或文本过短返回 None。
    """
    try:
        session = shared_session()
        resp = session.get(doc_url)
        resp.raise_for_status()
        content = resp.content
    except Exception as e:
        logger.warning(f"  [Word文档] 下载失败 {doc_url}: {e}")
        return None

    fmt = _detect_doc_format(content)
    if fmt == "ole":
        text = _extract_text_from_ole(content)
    elif fmt == "ooxml":
        text = _extract_text_from_ooxml(content)
    elif fmt == "pdf":
        # 极少数 Word 链接实际指向 PDF
        progress("  [Word文档] 实际为 PDF，转用 PDF 提取")
        text = _extract_text_from_pdf_bytes(content)
    else:
        logger.warning(f"  [Word文档] 未知格式（magic={content[:8].hex()}），尝试 OLE 回退")
        text = _extract_text_from_ole(content)

    if not text or len(text) <= 50:
        logger.warning(f"  [Word文档] 提取文本过短（{len(text or '')} 字符，格式={fmt}）")
        return None

    text = re.sub(r"\n{3,}", "\n\n", text)
    progress(f"  [Word文档] 提取成功（格式={fmt}），{len(text)} 字符")
    return text
