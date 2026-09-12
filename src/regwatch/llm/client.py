"""OpenAI 兼容的大模型客户端。

设计要点：

1. **单一实现**：所有模型都走 ``openai`` SDK 的 ``chat.completions.create``。
   厂商差异（如 ``max_completion_tokens``、``thinking`` 参数）改由
   :class:`~regwatch.domain.ModelProfile` 的 ``token_param`` / ``extra`` 声明式表达。
2. **参数自动降级**：端点不认识某个可选参数时，剥离后重试；
   降级次数有硬上限，不会无限循环。
3. **推理内容兜底**：部分模型把答案放在 ``reasoning_content``，
   :meth:`ChatResult.best_text` 自动回退。
4. **密钥不外泄**：日志与错误信息中的密钥一律脱敏。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain import ModelProfile
from ..settings import ConfigStore

__all__ = [
    "ChatResult",
    "LLMClient",
    "LLMClientFactory",
    "LLMError",
    "Usage",
]

logger = logging.getLogger("regwatch.llm")

DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF = 2.0

#: 单次调用中最多允许剥离多少个不兼容参数（防止无限降级循环）
MAX_DEGRADATIONS = 6

#: 请求中不允许被自动剥离的关键参数
_PROTECTED_KEYS = frozenset({"model", "messages"})

#: 出现这些关键词说明端点不认识某个参数，可剥离后重试
_PARAM_ERROR_HINTS = (
    "unrecognized",
    "unknown",
    "unsupported",
    "not support",
    "does not support",
    "extra inputs",
    "unexpected keyword",
    "invalid_request",
    "invalid request",
    "不支持",
    "无法识别",
    "未知参数",
    "多余的",
)

#: 出现这些关键词属于瞬时故障，可退避重试
_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "rate limit",
    "too many requests",
    "429",
    "500",
    "502",
    "503",
    "504",
    "overloaded",
    "temporarily",
    "service unavailable",
    "server error",
)


class LLMError(RuntimeError):
    """模型调用失败。"""


class ChatClient(Protocol):
    """供上层依赖注入的最小对话能力协议（便于测试替换）。"""

    def chat_text(self, prompt: str, system: str | None = None, **kwargs: Any) -> str: ...

    def chat(self, messages: Sequence[dict[str, Any]], **kwargs: Any) -> ChatResult: ...


# ──────────────────────────── 结果对象 ────────────────────────────


def _as_text(value: Any) -> str:
    """把模型返回的 content / reasoning 统一转成字符串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or ""))
        return "".join(parts)
    return str(value)


@dataclass
class Usage:
    """token 用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_raw(cls, raw: Any) -> Usage:
        if raw is None:
            return cls()
        return cls(
            prompt_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(raw, "total_tokens", 0) or 0),
        )


@dataclass
class ChatResult:
    """一次对话调用的结果。"""

    content: str = ""
    reasoning: str = ""
    model: str = ""
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)

    def best_text(self, min_reasoning_len: int = 80) -> str:
        """返回可用文本：优先 ``content``，为空时回退 ``reasoning_content``。"""
        if self.content and self.content.strip():
            return self.content
        if self.reasoning and len(self.reasoning.strip()) >= min_reasoning_len:
            return self.reasoning
        return self.content or ""

    def is_empty(self) -> bool:
        return not (self.content.strip() or self.reasoning.strip())


# ──────────────────────────── 客户端 ────────────────────────────


class LLMClient:
    """针对单个 :class:`ModelProfile` 的 OpenAI 兼容客户端。"""

    def __init__(
        self,
        profile: ModelProfile,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
    ) -> None:
        self.profile = profile
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff = backoff
        self._client: Any = None

    # ── 属性 ──

    @property
    def model(self) -> str:
        return self.profile.model

    @property
    def vision_model(self) -> str:
        return self.profile.resolved_vision_model()

    @property
    def client(self) -> Any:
        """惰性创建 OpenAI 客户端，避免无模型需求时强制依赖。"""
        if self._client is None:
            missing = self.profile.missing_fields()
            if missing:
                raise LLMError(f"模型配置「{self.profile.display_name}」缺少：{'、'.join(missing)}")
            if not self.profile.api_key:
                raise LLMError(
                    f"模型配置「{self.profile.display_name}」未提供 API Key"
                    f"（可填写在网页端配置页，或设置环境变量 "
                    f"{ConfigStore.env_key_name(self.profile.id)}）"
                )
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise LLMError("未安装 openai SDK，请执行：uv sync 或 pip install -e .") from exc
            self._client = OpenAI(
                api_key=self.profile.api_key,
                base_url=self.profile.base_url,
                timeout=self.timeout,
                max_retries=0,  # 重试由本类统一控制，避免双重退避
            )
        return self._client

    # ── 调用 ──

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = 8000,
        temperature: float | None = 0.1,
        extra: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """发起一次对话调用。

        Args:
            messages: OpenAI 格式消息列表。
            model: 覆盖默认模型名。
            max_tokens: 输出上限；为 ``None`` 时不传该参数。
            temperature: 采样温度；为 ``None`` 时不传该参数。
            extra: 调用级附加参数，覆盖 ``ModelProfile.extra`` 中的同名键。
            response_format: 结构化输出约束，如 ``{"type": "json_object"}``。
        """
        target_model = (model or self.profile.model or "").strip()
        if not target_model:
            raise LLMError(f"模型配置「{self.profile.display_name}」未指定模型名称")

        kwargs: dict[str, Any] = {"model": target_model, "messages": list(messages)}

        if max_tokens:
            token_param = self.profile.token_param or "max_tokens"
            kwargs[token_param] = int(max_tokens)
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format:
            kwargs["response_format"] = response_format

        merged_extra: dict[str, Any] = dict(self.profile.extra or {})
        merged_extra.update(extra or {})
        merged_extra.pop("token_param", None)
        for key, value in merged_extra.items():
            if value is not None:
                kwargs[key] = value

        return self._invoke(kwargs)

    def chat_text(self, prompt: str, system: str | None = None, **kwargs: Any) -> str:
        """便捷方法：单轮对话并直接返回文本。"""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, **kwargs).best_text()

    def vision(
        self,
        file_url: str,
        prompt: str,
        max_tokens: int = 4000,
        temperature: float = 0.1,
        content_type: str | None = None,
    ) -> str:
        """把远端 PDF / 图片地址交给视觉模型识别并返回文本。

        不同端点对「文件」消息的类型命名不同（``image_url`` / ``file_url``），
        由 ``ModelProfile.vision_content_type`` 指定，失败时自动互换重试。
        """
        content_type = content_type or self.profile.vision_content_type or "image_url"
        alt_type = "file_url" if content_type == "image_url" else "image_url"

        def message_part(kind: str) -> dict[str, Any]:
            return {"type": kind, kind: {"url": file_url}}

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [message_part(content_type), {"type": "text", "text": prompt}],
            }
        ]

        try:
            return self.chat(
                messages,
                model=self.vision_model,
                max_tokens=max_tokens,
                temperature=temperature,
            ).best_text()
        except LLMError as exc:
            if content_type == alt_type:
                raise
            logger.warning(
                "视觉消息类型 %s 不被端点接受，改用 %s 重试：%s",
                content_type,
                alt_type,
                self._mask(str(exc))[:200],
            )
            messages[0]["content"][0] = message_part(alt_type)
            return self.chat(
                messages,
                model=self.vision_model,
                max_tokens=max_tokens,
                temperature=temperature,
            ).best_text()

    # ── 连通性测试 ──

    def test_connection(self) -> tuple[bool, str]:
        """最小代价验证配置可用性，返回 ``(是否成功, 说明)``。"""
        missing = self.profile.missing_fields()
        if missing:
            return False, f"配置不完整，缺少：{'、'.join(missing)}"
        if not self.profile.api_key:
            return False, "未填写 API Key"

        # 1) 优先列出模型列表（成本最低）
        try:
            response = self.client.models.list()
            ids = [
                str(getattr(item, "id", "") or "")
                for item in (getattr(response, "data", None) or [])
            ]
            ids = [item for item in ids if item]
            if ids:
                hint = ""
                if self.profile.model and self.profile.model not in ids:
                    hint = f"；注意当前模型 {self.profile.model} 不在返回列表中"
                return True, f"连接成功，端点返回 {len(ids)} 个可用模型{hint}"
        except Exception as exc:
            logger.debug("models.list 不可用，回退到最小对话：%s", self._mask(str(exc)))

        # 2) 回退：发一次极小的真实对话
        try:
            result = self.chat([{"role": "user", "content": "ping"}], max_tokens=512, temperature=0)
            if result.is_empty():
                return False, "连接成功但模型返回空内容，请检查模型名称是否正确"
            return True, f"连接成功，模型 {result.model or self.profile.model} 响应正常"
        except LLMError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, self._mask(f"{type(exc).__name__}: {exc}")

    # ── 内部实现 ──

    def _invoke(self, kwargs: dict[str, Any]) -> ChatResult:
        """执行请求：参数降级（有上限）+ 瞬时错误退避重试（有上限）。"""
        working = dict(kwargs)
        attempt = 0
        degradations = 0
        last_error: BaseException | None = None

        while True:
            try:
                return self._to_result(self.client.chat.completions.create(**working))
            except Exception as exc:
                last_error = exc

                if degradations < MAX_DEGRADATIONS:
                    degraded = self._degrade(working, exc)
                    if degraded:
                        degradations += 1
                        logger.warning("端点不接受参数 %s，已自动剥离后重试", degraded)
                        continue

                if attempt < self.max_retries and self._is_transient(exc):
                    wait = self.backoff * (2**attempt)
                    attempt += 1
                    logger.warning(
                        "模型调用失败（%s），%.1fs 后重试 %d/%d",
                        type(exc).__name__,
                        wait,
                        attempt,
                        self.max_retries,
                    )
                    time.sleep(wait)
                    continue

                break

        raise LLMError(self._format_error(last_error)) from last_error

    def _degrade(self, working: dict[str, Any], exc: BaseException) -> str | None:
        """从错误信息中定位不兼容参数并剥离，返回被剥离的参数说明。"""
        message = str(exc).lower()
        if not any(hint in message for hint in _PARAM_ERROR_HINTS):
            return None

        for key in list(working.keys()):
            if key in _PROTECTED_KEYS:
                continue
            if key.lower() not in message:
                continue

            value = working.pop(key)

            # max_tokens 与 max_completion_tokens 互换而非直接丢弃
            if key == "max_tokens":
                working["max_completion_tokens"] = value
                return "max_tokens → max_completion_tokens"
            if key == "max_completion_tokens":
                working["max_tokens"] = value
                return "max_completion_tokens → max_tokens"
            return f"{key}={value!r}"

        return None

    @staticmethod
    def _is_transient(exc: BaseException) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        message = f"{type(exc).__name__} {exc}".lower()
        return any(hint in message for hint in _TRANSIENT_HINTS)

    @staticmethod
    def _to_result(response: Any) -> ChatResult:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMError("模型响应中没有 choices 字段")

        choice = choices[0]
        message = getattr(choice, "message", None)
        content = _as_text(getattr(message, "content", None))
        reasoning = _as_text(getattr(message, "reasoning_content", None)) or _as_text(
            getattr(message, "reasoning", None)
        )

        return ChatResult(
            content=content,
            reasoning=reasoning,
            model=str(getattr(response, "model", "") or ""),
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
            usage=Usage.from_raw(getattr(response, "usage", None)),
        )

    def _mask(self, text: str) -> str:
        """把文本中的密钥替换为掩码。"""
        key = self.profile.api_key or ""
        if key and len(key) > 8:
            return text.replace(key, self.profile.masked_key)
        return text

    def _format_error(self, exc: BaseException | None) -> str:
        if exc is None:
            return "未知错误"
        return self._mask(f"{type(exc).__name__}: {exc}")


# ──────────────────────────── 客户端工厂 ────────────────────────────


def profile_signature(profile: ModelProfile) -> str:
    """客户端缓存键。

    旧版用 ``abs(hash(api_key))``，会受 ``PYTHONHASHSEED`` 随机化影响导致
    跨进程缓存键不稳定；这里改用稳定摘要，且只记录密钥长度与哈希摘要。
    """
    key = profile.api_key or ""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return "|".join(
        [
            profile.id,
            profile.base_url,
            profile.model,
            profile.vision_model,
            profile.token_param,
            str(len(key)),
            digest,
            json.dumps(profile.extra or {}, sort_keys=True, ensure_ascii=False),
        ]
    )


class LLMClientFactory:
    """按任务 / 模型 ID 解析配置并缓存客户端。

    取代旧版的模块级 ``_clients`` 字典：缓存随工厂实例走，
    配置变更时只需丢弃工厂，不再依赖进程级全局状态。
    """

    def __init__(
        self,
        models: Sequence[ModelProfile] = (),
        tasks: dict[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._models = tuple(models)
        self._tasks = dict(tasks or {})
        self._timeout = timeout
        self._max_retries = max_retries
        self._clients: dict[str, LLMClient] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> LLMClientFactory:
        """从 :class:`~regwatch.settings.Settings` 构造。"""
        return cls(settings.models, settings.tasks, **kwargs)

    # ── 配置 ──

    def refresh(self, models: Sequence[ModelProfile], tasks: dict[str, str] | None = None) -> None:
        """更新模型配置并清空缓存（配置变更后调用）。"""
        with self._lock:
            self._models = tuple(models)
            if tasks is not None:
                self._tasks = dict(tasks)
            self._clients.clear()

    def model(self, model_id: str) -> ModelProfile | None:
        for profile in self._models:
            if profile.id == model_id:
                return profile
        return None

    def resolve(self, model_id: str | None = None, task: str | None = None) -> ModelProfile:
        if model_id:
            profile = self.model(model_id)
            if profile is None:
                raise LLMError(f"未找到模型配置：{model_id}")
            return profile
        if task:
            bound = self._tasks.get(task, "")
            if bound:
                profile = self.model(bound)
                if profile is not None:
                    return profile
        if self._models:
            return self._models[0]
        raise LLMError(
            "尚未配置任何模型。请在网页端「模型与配置」页填写 "
            "接口地址（base_url）、API Key 与模型名称后保存。"
        )

    # ── 客户端 ──

    def client(self, model_id: str | None = None, task: str | None = None) -> LLMClient:
        profile = self.resolve(model_id=model_id, task=task)
        signature = profile_signature(profile)
        with self._lock:
            cached = self._clients.get(signature)
            if cached is not None:
                return cached
            client = LLMClient(profile, timeout=self._timeout, max_retries=self._max_retries)
            self._clients[signature] = client
            return client

    def test_profile(self, profile: ModelProfile, timeout: float = 30.0) -> tuple[bool, str]:
        """直接测试一条配置的连通性（不进缓存）。"""
        return LLMClient(profile, timeout=timeout, max_retries=1).test_connection()
