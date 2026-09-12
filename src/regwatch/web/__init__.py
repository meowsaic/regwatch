"""Streamlit 网页端。"""

from __future__ import annotations

from pathlib import Path

__all__ = ["app_path"]


def app_path() -> Path:
    """网页入口文件路径（供 ``streamlit run`` 使用）。"""
    return Path(__file__).parent / "app.py"
