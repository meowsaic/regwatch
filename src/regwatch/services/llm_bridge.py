"""配置 → 模型客户端工厂的适配层。

单独成模块是为了让 :mod:`regwatch.services.container` 不必关心
「从哪来模型配置」这一细节，测试时可整块替换。
"""

from __future__ import annotations

from ..llm import LLMClientFactory
from ..settings import Settings

__all__ = ["build_llm_factory"]


def build_llm_factory(settings: Settings) -> LLMClientFactory:
    """按配置构造模型客户端工厂（模型条目与任务绑定一并传入）。"""
    return LLMClientFactory.from_settings(settings)
