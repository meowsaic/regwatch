"""BeautifulSoup 类型收窄辅助（仅用于通过 mypy，不改变运行时行为）。"""

from __future__ import annotations

from bs4 import Tag


def as_tag(node: object) -> Tag | None:
    """把 ``find`` / ``or`` 链的结果收窄为 ``Tag | None``。"""
    return node if isinstance(node, Tag) else None


def attr_str(tag: Tag | None, name: str, default: str = "") -> str:
    """读取标签属性并归一为 ``str``（多值属性取首个）。"""
    if tag is None:
        return default
    value = tag.get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        first = value[0]
        return first if isinstance(first, str) else str(first)
    return default
