"""模型接入子包。

对外只需 :class:`LLMClientFactory` 一个入口：它按任务 / 模型 ID 解析配置
并缓存客户端，测试时用假实现替换即可，无需 monkeypatch 全局单例。
"""

from __future__ import annotations

from .client import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    MAX_DEGRADATIONS,
    ChatClient,
    ChatResult,
    LLMClient,
    LLMClientFactory,
    LLMError,
    Usage,
    profile_signature,
)
from .parsing import parse_json_response, strip_code_fence

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    "MAX_DEGRADATIONS",
    "ChatClient",
    "ChatResult",
    "LLMClient",
    "LLMClientFactory",
    "LLMError",
    "Usage",
    "parse_json_response",
    "profile_signature",
    "strip_code_fence",
]
