"""统一大模型客户端（OpenAI 兼容协议）。

设计要点：

1. **单一实现**：所有模型都走 ``openai`` SDK 的 ``chat.completions.create``，
   历史上按厂商分叉的差异（MiMo 需要 ``max_completion_tokens``、智谱需要
   ``thinking`` 参数等）改由 ``ModelProfile.token_param`` 与 ``ModelProfile.extra``
   声明式表达，不再写 if/elif 分支。
2. **参数自动降级**：若目标端点不认识某个可选参数，捕获异常、剥离该参数后
   立即重试，从而在不牺牲兼容性的前提下适配任意 OpenAI 兼容服务。
3. **推理内容兜底**：部分模型把答案放在 ``reasoning_content`` 而 ``content``
   为空，:meth:`ChatResult.best_text` 会自动回退。
4. **密钥不外泄**：日志与错误信息中的密钥一律脱敏。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .config import Config, ConfigError, get_config
from .datamodels import ModelProfile

__all__ = [
    "ChatResult",
    "LLMClient",
    "LLMError",
    "Usage",
    "get_llm",
    "parse_json_response",
    "reset_clients",
]

logger = logging.getLogger("regwatch.llm")

_DEFAULT_TIMEOUT = 180.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF = 2.0

# 请求中不允许被自动剥离的关键参数
_PROTECTED_KEYS = ("model", "messages")

# 出现这些关键词的错误，说明是端点不认识的参数，可剥离后重试
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

# 出现这些关键词的错误属于瞬时故障，可退避重试
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


# ──────────────────────────── 结果对象 ────────────────────────────


def _as_text(value: Any) -> str:
    """把模型返回的 content / reasoning 统一转成字符串。

    部分端点会返回内容分片列表，这里做拼接兜底。
    """
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
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff: float = _DEFAULT_BACKOFF,
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
                    f"{Config.env_key_name(self.profile.id)}）"
                )
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise LLMError(
                    "未安装 openai SDK，请执行：pip install -r requirements.txt"
                ) from exc
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

        # 附加参数：条目级 extra 打底，调用级 extra 覆盖；None 值表示显式省略
        merged_extra: dict[str, Any] = dict(self.profile.extra or {})
        merged_extra.update(extra or {})
        merged_extra.pop("token_param", None)
        for key, value in merged_extra.items():
            if value is not None:
                kwargs[key] = value

        return self._invoke(kwargs)

    def chat_text(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs: Any,
    ) -> str:
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

        def _message_part(kind: str) -> dict[str, Any]:
            # 智谱等端点要求 content[0] 必须带 type 字段，如
            # {"type": "file_url", "file_url": {"url": ...}}（OpenAI 同构）
            return {"type": kind, kind: {"url": file_url}}

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    _message_part(content_type),
                    {"type": "text", "text": prompt},
                ],
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
            messages[0]["content"][0] = _message_part(alt_type)
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
        model_error = ""
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
            model_error = str(exc)
            logger.debug("models.list 不可用，回退到最小对话：%s", self._mask(model_error))

        # 2) 回退：发一次极小的真实对话。
        # max_tokens 给足余量：GLM 等模型会把思考过程计入输出上限，
        # 上限过小会导致正文为空而被误判为"返回空内容"。
        try:
            result = self.chat(
                [{"role": "user", "content": "ping"}],
                max_tokens=512,
                temperature=0,
            )
            if result.is_empty():
                return False, "连接成功但模型返回空内容，请检查模型名称是否正确"
            return True, f"连接成功，模型 {result.model or self.profile.model} 响应正常"
        except LLMError as exc:
            return False, str(exc)
        except Exception as exc:
            return False, self._mask(f"{type(exc).__name__}: {exc}")

    # ── 内部实现 ──

    def _invoke(self, kwargs: dict[str, Any]) -> ChatResult:
        """执行请求：参数降级 + 瞬时错误退避重试。"""
        working = dict(kwargs)
        attempt = 0
        last_error: BaseException | None = None

        while True:
            try:
                response = self.client.chat.completions.create(**working)
                return self._to_result(response)
            except Exception as exc:
                last_error = exc

                degraded = self._degrade(working, exc)
                if degraded:
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
        """从错误信息中定位不兼容参数并将其剥离，返回被剥离的参数说明。"""
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

_clients: dict[str, LLMClient] = {}
_clients_lock = threading.Lock()


def _signature(profile: ModelProfile) -> str:
    return "|".join(
        [
            profile.id,
            profile.base_url,
            profile.model,
            profile.vision_model,
            profile.token_param,
            str(len(profile.api_key)),
            str(abs(hash(profile.api_key))),
            json.dumps(profile.extra or {}, sort_keys=True, ensure_ascii=False),
        ]
    )


def get_llm(
    task: str | None = None,
    model_id: str | None = None,
    config: Config | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    force_new: bool = False,
) -> LLMClient:
    """按任务或模型 ID 获取客户端（带缓存）。

    Args:
        task: 任务类型（``summarize`` / ``report`` / ``vision``），
            按配置中的任务映射解析模型。
        model_id: 直接指定模型配置 ID，优先级高于 ``task``。
        config: 配置对象；默认使用全局单例。
        force_new: 跳过缓存，重新创建客户端。
    """
    cfg = config or get_config()
    profile = cfg.resolved_profile(model_id=model_id, task=task)
    if profile is None:  # pragma: no cover - resolved_profile 默认 required=True
        raise ConfigError("没有可用的模型配置")

    signature = _signature(profile)
    with _clients_lock:
        cached = _clients.get(signature)
        if cached is not None and not force_new:
            return cached
        client = LLMClient(profile, timeout=timeout, max_retries=max_retries)
        _clients[signature] = client
        return client


def reset_clients() -> None:
    """清空客户端缓存（配置变更后调用）。"""
    with _clients_lock:
        _clients.clear()


def test_profile(profile: ModelProfile, timeout: float = 30.0) -> tuple[bool, str]:
    """直接测试一条模型配置的连通性（不经过缓存与全局配置）。"""
    client = LLMClient(profile, timeout=timeout, max_retries=1)
    return client.test_connection()


# ──────────────────────────── JSON 解析 ────────────────────────────


def _strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 围栏。"""
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
    """依次产出候选 JSON 片段：整串 → 每个 ``{`` 起始的平衡对象。"""
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
    """从模型输出中稳健地解析 JSON 对象。

    兼容：markdown 代码块包裹、前后夹杂解释文字、尾随逗号。
    解析失败返回 ``None``，由调用方决定是否重试。
    """
    if not text or not text.strip():
        return None

    cleaned = _strip_code_fence(text)
    for candidate in _iter_json_candidates(cleaned):
        for attempt in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                result = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict):
                return result
    return None
