"""统一模型客户端的纯本地测试（使用假客户端，不发网络请求）。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from regwatch.datamodels import ModelProfile
from regwatch.llm import (
    ChatResult,
    LLMClient,
    LLMError,
    Usage,
    parse_json_response,
)


# ──────────────────────────── 假客户端 ────────────────────────────


def make_response(content: str = "ok", reasoning: str = "", model: str = "fake-model"):
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    return SimpleNamespace(choices=[choice], model=model, usage=usage)


class FakeClient:
    """按脚本依次返回结果或抛出异常，并记录每次调用的参数。"""

    def __init__(self, script=None, models=None):
        self.script = list(script or [])
        self.calls = []
        self._models = models
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=self._list_models)

    def _list_models(self):
        if isinstance(self._models, BaseException):
            raise self._models
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in (self._models or [])])

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else make_response()
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(kwargs)
        return item


def build_client(script=None, models=None, **profile_kwargs) -> tuple[LLMClient, FakeClient]:
    defaults = dict(id="t", base_url="https://example.invalid/v1", api_key="sk-test-1234567890", model="fake-model")
    defaults.update(profile_kwargs)
    profile = ModelProfile(**defaults)
    client = LLMClient(profile, backoff=0.01, max_retries=2)
    fake = FakeClient(script=script, models=models)
    client._client = fake  # 绕过 OpenAI SDK 初始化
    return client, fake


# ──────────────────────────── 测试 ────────────────────────────


class ParseJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_json_response('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        text = '```json\n{"violation_type": "违规募集"}\n```'
        self.assertEqual(parse_json_response(text), {"violation_type": "违规募集"})

    def test_fence_without_language(self):
        self.assertEqual(parse_json_response('```\n{"a": 2}\n```'), {"a": 2})

    def test_surrounding_text(self):
        text = '好的，以下是结果：\n{"a": 3, "b": "中文"}\n以上。'
        self.assertEqual(parse_json_response(text), {"a": 3, "b": "中文"})

    def test_trailing_comma(self):
        self.assertEqual(parse_json_response('{"a": 1, "b": 2,}'), {"a": 1, "b": 2})

    def test_nested_object(self):
        text = '前置说明 {"outer": {"inner": [1, 2]}, "n": 5} 尾巴'
        self.assertEqual(parse_json_response(text), {"outer": {"inner": [1, 2]}, "n": 5})

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_json_response(""))
        self.assertIsNone(parse_json_response("完全没有 JSON"))
        self.assertIsNone(parse_json_response("[1, 2, 3]"))


class ChatResultTests(unittest.TestCase):
    def test_best_text_prefers_content(self):
        result = ChatResult(content="答案", reasoning="推理" * 100)
        self.assertEqual(result.best_text(), "答案")

    def test_best_text_falls_back_to_reasoning(self):
        result = ChatResult(content="", reasoning="推理内容" * 30)
        self.assertEqual(result.best_text().strip(), ("推理内容" * 30).strip())

    def test_short_reasoning_not_used(self):
        result = ChatResult(content="", reasoning="短")
        self.assertEqual(result.best_text(min_reasoning_len=80), "")

    def test_empty(self):
        self.assertTrue(ChatResult().is_empty())
        self.assertFalse(ChatResult(content="x").is_empty())

    def test_usage_from_raw(self):
        self.assertEqual(Usage.from_raw(None), Usage())
        usage = Usage.from_raw(SimpleNamespace(prompt_tokens=5, completion_tokens=6, total_tokens=11))
        self.assertEqual((usage.prompt_tokens, usage.completion_tokens, usage.total_tokens), (5, 6, 11))


class ChatCallTests(unittest.TestCase):
    def test_basic_call_builds_kwargs(self):
        client, fake = build_client()
        result = client.chat([{"role": "user", "content": "hi"}], max_tokens=100, temperature=0.2)
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.usage.total_tokens, 3)
        self.assertEqual(result.model, "fake-model")
        kwargs = fake.calls[0]
        self.assertEqual(kwargs["model"], "fake-model")
        self.assertEqual(kwargs["max_tokens"], 100)
        self.assertEqual(kwargs["temperature"], 0.2)
        self.assertNotIn("max_completion_tokens", kwargs)

    def test_token_param_switch(self):
        client, fake = build_client(token_param="max_completion_tokens")
        client.chat([{"role": "user", "content": "hi"}], max_tokens=64)
        self.assertEqual(fake.calls[0]["max_completion_tokens"], 64)
        self.assertNotIn("max_tokens", fake.calls[0])

    def test_extra_merged_and_call_level_wins(self):
        client, fake = build_client(extra={"top_p": 0.5, "thinking": {"type": "disabled"}})
        client.chat([{"role": "user", "content": "hi"}], extra={"top_p": 0.9})
        kwargs = fake.calls[0]
        self.assertEqual(kwargs["top_p"], 0.9)
        self.assertEqual(kwargs["thinking"], {"type": "disabled"})

    def test_none_temperature_omitted(self):
        client, fake = build_client()
        client.chat([{"role": "user", "content": "hi"}], temperature=None)
        self.assertNotIn("temperature", fake.calls[0])

    def test_missing_model_raises(self):
        client, _ = build_client(model="")
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_reasoning_fallback_used(self):
        client, _ = build_client(script=[make_response(content="", reasoning="推理" * 60)])
        result = client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result.best_text().strip(), ("推理" * 60).strip())


class DegradeTests(unittest.TestCase):
    def test_strip_unsupported_temperature(self):
        error = ValueError("Unrecognized request argument supplied: temperature")
        client, fake = build_client(script=[error, make_response("ok")])
        result = client.chat([{"role": "user", "content": "hi"}], temperature=0.7)
        self.assertEqual(result.content, "ok")
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("temperature", fake.calls[0])
        self.assertNotIn("temperature", fake.calls[1])

    def test_swap_max_tokens(self):
        error = ValueError("unknown parameter max_tokens is not supported")
        client, fake = build_client(script=[error, make_response("ok")])
        client.chat([{"role": "user", "content": "hi"}], max_tokens=32)
        self.assertEqual(fake.calls[1]["max_completion_tokens"], 32)
        self.assertNotIn("max_tokens", fake.calls[1])

    def test_strip_custom_extra_param(self):
        error = ValueError("Extra inputs are not permitted: thinking")
        client, fake = build_client(script=[error, make_response("ok")], extra={"thinking": {"type": "disabled"}})
        client.chat([{"role": "user", "content": "hi"}])
        self.assertIn("thinking", fake.calls[0])
        self.assertNotIn("thinking", fake.calls[1])

    def test_protected_params_not_stripped(self):
        # 错误提到 model 时不应剥离 model，应直接抛出
        error = ValueError("Unrecognized request argument supplied: model")
        client, _ = build_client(script=[error, error, error, error])
        with self.assertRaises(LLMError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_retries_transient_then_succeeds(self):
        client, fake = build_client(script=[TimeoutError("timeout"), make_response("ok")])
        self.assertEqual(client.chat([{"role": "user", "content": "hi"}]).content, "ok")
        self.assertEqual(len(fake.calls), 2)

    def test_error_message_masks_api_key(self):
        secret = "sk-secret-abcdefghijklmn"
        error = ValueError(f"auth failed for key {secret}")
        client, _ = build_client(script=[error] * 5, api_key=secret)
        with self.assertRaises(LLMError) as ctx:
            client.chat([{"role": "user", "content": "hi"}])
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("****", str(ctx.exception))


class ConnectionTests(unittest.TestCase):
    def test_missing_key(self):
        client, _ = build_client(api_key="")
        ok, message = client.test_connection()
        self.assertFalse(ok)
        self.assertIn("API Key", message)

    def test_missing_base_url(self):
        client, _ = build_client(base_url="")
        ok, message = client.test_connection()
        self.assertFalse(ok)
        self.assertIn("base_url", message)

    def test_success_via_model_list(self):
        client, _ = build_client(models=["fake-model", "other"])
        ok, message = client.test_connection()
        self.assertTrue(ok, message)
        self.assertIn("2 个可用模型", message)

    def test_model_list_warns_on_unknown_model(self):
        client, _ = build_client(models=["other"])
        ok, message = client.test_connection()
        self.assertTrue(ok)
        self.assertIn("不在返回列表中", message)

    def test_fallback_to_chat_when_list_unsupported(self):
        client, _ = build_client(
            script=[make_response("pong")],
            models=ValueError("models endpoint not supported"),
        )
        ok, message = client.test_connection()
        self.assertTrue(ok, message)
        self.assertIn("响应正常", message)

    def test_failure_reported(self):
        client, _ = build_client(script=[ValueError("bad api key")] * 4,
                                models=ValueError("nope"))
        ok, message = client.test_connection()
        self.assertFalse(ok)
        self.assertIn("bad api key", message)


class ProfileTests(unittest.TestCase):
    def test_masked_key(self):
        self.assertEqual(ModelProfile(id="x", api_key="").masked_key, "（未填写）")
        self.assertEqual(ModelProfile(id="x", api_key="short").masked_key, "****")
        self.assertEqual(
            ModelProfile(id="x", api_key="sk-1234567890abcdef").masked_key,
            "sk-1****cdef",
        )

    def test_missing_fields(self):
        self.assertEqual(ModelProfile(id="").missing_fields(), ["配置标识", "接口地址 base_url", "文本模型 model"])
        self.assertEqual(ModelProfile(id="a", base_url="u", model="m").missing_fields(), [])

    def test_vision_model_falls_back(self):
        self.assertEqual(ModelProfile(id="a", model="m").resolved_vision_model(), "m")
        self.assertEqual(ModelProfile(id="a", model="m", vision_model="v").resolved_vision_model(), "v")


if __name__ == "__main__":
    unittest.main()
