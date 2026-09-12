"""模型输出解析：从自由文本中稳健地提取 JSON 对象。

模型常常在 JSON 外面裹一层 markdown 代码块，或前后夹带解释文字，
偶发尾随逗号。这里按「整串 → 每个 ``{`` 起始的平衡对象 → 去掉尾随逗号」
的顺序依次尝试，全部失败返回 ``None``，由调用方决定是否重试。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["parse_json_response", "strip_code_fence"]


def strip_code_fence(text: str) -> str:
    """去掉 ```` ```json ... ``` ```` 围栏。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _iter_json_candidates(text: str):
    """依次产出候选 JSON 片段。"""
    yield text
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield text[index : index + end]


def parse_json_response(text: str) -> dict[str, Any] | None:
    """从模型输出中解析 JSON 对象，失败返回 ``None``。"""
    if not text or not text.strip():
        return None

    cleaned = strip_code_fence(text)
    for candidate in _iter_json_candidates(cleaned):
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                result = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict):
                return result
    return None
