"""模型客户端测试：参数组装、降级有上限、缓存键稳定与 JSON 解析。"""

from __future__ import annotations

from typing import Any

import pytest

from regwatch.domain import ModelProfile
from regwatch.llm import (
    MAX_DEGRADATIONS,
    ChatResult,
    LLMClient,
    LLMClientFactory,
    LLMError,
    profile_signature,
)
from regwatch.llm.parsing import parse_json_response, strip_code_fence


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reasoning_content = None


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)
        self.finish_reason = "stop"


class FakeResponse:
    def __init__(self, content: str, model: str = "m") -> None:
        self.choices = [FakeChoice(content)]
        self.model = model
        self.usage = None


class FakeCompletions:
    """记录每次调用的参数，并可按预设次数抛错。"""

    def __init__(self, content: str = '{"ok": true}', errors: list[str] | None = None) -> None:
        self.content = content
        self.errors = list(errors or [])
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(dict(kwargs))
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        return FakeResponse(self.content)


class FakeOpenAI:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


class FakeChoiceLessResponse:
    """模拟「HTTP 200 + 只含 error、没有 choices」的网关响应。"""

    def __init__(self, error: dict[str, Any] | None = None, response_id: str = "gen-test") -> None:
        if error is not None:
            self.error = error
        self.id = response_id


class FakeSequenceCompletions:
    """按顺序吐出预置响应，用于验证重试次数。"""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        return self.responses.pop(0)


def _client_with_responses(
    *responses: Any, max_retries: int = 0
) -> tuple[LLMClient, FakeSequenceCompletions]:
    profile = ModelProfile(id="fake", base_url="http://x", api_key="sk-1234567890abcdef", model="m")
    client = LLMClient(profile, max_retries=max_retries, backoff=0)
    completions = FakeSequenceCompletions(list(responses))
    client._client = FakeOpenAI(completions)
    return client, completions


def _client(**profile_kwargs: Any) -> LLMClient:
    profile = ModelProfile(
        id="fake", base_url="http://x", api_key="sk-1234567890abcdef", model="m", **profile_kwargs
    )
    return LLMClient(profile, max_retries=0)


def _attach(client: LLMClient, completions: FakeCompletions) -> None:
    client._client = FakeOpenAI(completions)


class TestChatResult:
    def test_best_text_prefers_content(self) -> None:
        assert ChatResult(content="正文", reasoning="推" * 100).best_text() == "正文"

    def test_best_text_falls_back_to_reasoning(self) -> None:
        assert ChatResult(content="", reasoning="推" * 100).best_text() == "推" * 100

    def test_short_reasoning_is_not_used(self) -> None:
        assert ChatResult(content="", reasoning="短").best_text() == ""

    def test_is_empty(self) -> None:
        assert ChatResult().is_empty()


class TestChatParams:
    def test_token_param_is_honoured(self) -> None:
        client = _client(token_param="max_completion_tokens")
        completions = FakeCompletions()
        _attach(client, completions)
        client.chat([{"role": "user", "content": "hi"}], max_tokens=100)
        assert completions.calls[0]["max_completion_tokens"] == 100
        assert "max_tokens" not in completions.calls[0]

    def test_call_level_extra_overrides_profile_extra(self) -> None:
        client = _client(extra={"top_p": 0.9, "thinking": {"type": "disabled"}})
        completions = FakeCompletions()
        _attach(client, completions)
        client.chat([{"role": "user", "content": "hi"}], extra={"top_p": 0.5})
        kwargs = completions.calls[0]
        assert kwargs["top_p"] == 0.5
        assert kwargs["thinking"] == {"type": "disabled"}

    def test_none_extra_value_is_omitted(self) -> None:
        client = _client()
        completions = FakeCompletions()
        _attach(client, completions)
        client.chat([{"role": "user", "content": "hi"}], extra={"foo": None})
        assert "foo" not in completions.calls[0]

    def test_missing_model_raises(self) -> None:
        client = LLMClient(ModelProfile(id="x", base_url="http://x", api_key="sk-1"))
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])


class TestDegrade:
    def test_unknown_parameter_is_stripped_then_retried(self) -> None:
        client = _client()
        completions = FakeCompletions(errors=["unknown parameter: temperature"])
        _attach(client, completions)
        client.chat([{"role": "user", "content": "hi"}], temperature=0.1)
        assert "temperature" in completions.calls[0]
        assert "temperature" not in completions.calls[1]

    def test_degradation_is_bounded(self) -> None:
        client = _client()
        completions = FakeCompletions(errors=["unknown parameter: temperature"] * 50)
        _attach(client, completions)
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}], temperature=0.1)
        assert len(completions.calls) <= MAX_DEGRADATIONS + 1


class TestGatewayErrorBody:
    """网关在 200 之后失败：响应体只含 error、没有 choices（见 OpenRouter 文档）。"""

    def test_error_body_is_surfaced_in_message(self) -> None:
        client, _ = _client_with_responses(
            FakeChoiceLessResponse(
                {
                    "code": 429,
                    "message": "Rate limit exceeded",
                    "metadata": {"error_type": "rate_limit_exceeded"},
                }
            )
        )
        with pytest.raises(LLMError) as excinfo:
            client.chat([{"role": "user", "content": "hi"}])
        text = str(excinfo.value)
        assert "choices" in text
        assert "429" in text
        assert "rate_limit_exceeded" in text
        assert "gen-test" in text

    def test_transient_gateway_error_is_retried(self) -> None:
        client, completions = _client_with_responses(
            FakeChoiceLessResponse({"code": 503, "message": "Provider overloaded"}),
            FakeResponse('{"ok": true}'),
            max_retries=2,
        )
        assert client.chat([{"role": "user", "content": "hi"}]).content == '{"ok": true}'
        assert completions.calls == 2

    def test_content_policy_error_is_not_retried(self) -> None:
        client, completions = _client_with_responses(
            FakeChoiceLessResponse(
                {
                    "code": 403,
                    "message": "Output blocked",
                    "metadata": {"error_type": "content_policy_violation"},
                }
            ),
            FakeResponse('{"ok": true}'),
            max_retries=2,
        )
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])
        assert completions.calls == 1

    def test_response_without_error_detail_still_reports_choices(self) -> None:
        client, _ = _client_with_responses(FakeChoiceLessResponse())
        with pytest.raises(LLMError, match="choices"):
            client.chat([{"role": "user", "content": "hi"}])


class TestFactory:
    def test_signature_is_stable_and_key_sensitive(self) -> None:
        base = ModelProfile(id="a", model="m", api_key="sk-1")
        assert profile_signature(base) == profile_signature(base)
        assert profile_signature(base) != profile_signature(base.replace(api_key="sk-2"))

    def test_resolve_by_id_then_task_then_first(self) -> None:
        first = ModelProfile(id="first", model="m1")
        second = ModelProfile(id="second", model="m2")
        factory = LLMClientFactory([first, second], {"report": "second"})
        assert factory.resolve(model_id="second").id == "second"
        assert factory.resolve(task="report").id == "second"
        assert factory.resolve().id == "first"

    def test_resolve_unknown_id_raises(self) -> None:
        factory = LLMClientFactory([ModelProfile(id="a")])
        with pytest.raises(LLMError):
            factory.resolve(model_id="nope")

    def test_client_is_cached_and_refresh_clears(self) -> None:
        factory = LLMClientFactory([ModelProfile(id="a", model="m")])
        assert factory.client() is factory.client()
        factory.refresh([ModelProfile(id="a", model="m2")])
        assert factory.client().model == "m2"


class TestJsonParsing:
    def test_plain_object(self) -> None:
        assert parse_json_response('{"a": 1}') == {"a": 1}

    def test_code_fence_is_removed(self) -> None:
        assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_surrounding_text_is_tolerated(self) -> None:
        assert parse_json_response('说明：\n{"a": 1}\n以上') == {"a": 1}

    def test_trailing_comma_is_tolerated(self) -> None:
        assert parse_json_response('{"a": 1, }') == {"a": 1}

    def test_invalid_returns_none(self) -> None:
        assert parse_json_response("不是 JSON") is None
        assert parse_json_response("") is None
