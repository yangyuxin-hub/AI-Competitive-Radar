# -*- coding: utf-8 -*-
"""thinking 档位开关的优先级语义(纯本地,不打网络)。

设计契约:
- _thinking_extra_body(mode): 显式 mode > 环境变量 LLM_THINKING > 不传(None)
- deep_thinking_mode(): LLM_THINKING_DEEP 优先;未设置返回 None(调用方回退全局档位)
- 无效值一律视同未设置 → 不向 Ark 透传任何参数(零行为变化)
"""
import pytest

from src.llm import _is_thinking_rejected, _thinking_extra_body, deep_thinking_mode


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.delenv("LLM_THINKING_DEEP", raising=False)


def test_default_no_extra_body():
    assert _thinking_extra_body() is None


def test_env_disabled(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", "disabled")
    assert _thinking_extra_body() == {"thinking": {"type": "disabled"}}


def test_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", "disabled")
    assert _thinking_extra_body("enabled") == {"thinking": {"type": "enabled"}}


def test_invalid_value_means_not_sent(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", "yes-please")
    assert _thinking_extra_body() is None
    assert _thinking_extra_body("nonsense") is None


def test_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", " Auto ")
    assert _thinking_extra_body() == {"thinking": {"type": "auto"}}


def test_deep_mode_unset_falls_back_to_global(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", "disabled")
    assert deep_thinking_mode() is None  # None → call_json 内回退全局
    assert _thinking_extra_body(deep_thinking_mode()) == {"thinking": {"type": "disabled"}}


def test_deep_mode_overrides_global(monkeypatch):
    monkeypatch.setenv("LLM_THINKING", "disabled")
    monkeypatch.setenv("LLM_THINKING_DEEP", "enabled")
    assert deep_thinking_mode() == "enabled"
    assert _thinking_extra_body(deep_thinking_mode()) == {"thinking": {"type": "enabled"}}


def test_thinking_rejected_detection():
    # 实测 2026-06-11:Doubao-Seed-2.0-lite 对 auto 返回的真实报错
    real = Exception(
        "Error code: 400 - {'error': {'code': 'InvalidParameter', 'message': "
        "'Unsupported thinking type for the current model: auto', 'type': 'BadRequest'}}"
    )
    assert _is_thinking_rejected(real)
    assert not _is_thinking_rejected(Exception("AuthenticationError: The API key doesn't exist"))
    assert not _is_thinking_rejected(Exception("InvalidParameter: max_tokens too large"))


class _FlakyClient:
    """首次带 extra_body 调用抛 thinking 拒绝错,去掉后成功。"""
    class chat:  # noqa: N801 — 模拟 openai client 的属性链
        class completions:
            calls = []

            @classmethod
            def create(cls, **kwargs):
                cls.calls.append(dict(kwargs))
                if "extra_body" in kwargs:
                    raise Exception("InvalidParameter: Unsupported thinking type for the current model: auto")
                class _Msg:
                    content = '{"ok": true}'
                class _Choice:
                    message = _Msg()
                class _Resp:
                    choices = [_Choice()]
                    usage = None
                return _Resp()

    def with_options(self, **kw):
        return self


def test_call_json_retries_without_thinking(monkeypatch):
    from src.llm import LLMClient
    monkeypatch.setenv("LLM_THINKING", "auto")  # 模拟配错档位
    monkeypatch.setenv("LLM_TRACE", "0")        # 别把测试调用写进 llm_calls.jsonl
    c = LLMClient()
    c._client = _FlakyClient()
    out = c.call_json("sys", {"q": 1}, label="t")
    assert out == {"ok": True}
    calls = _FlakyClient.chat.completions.calls
    assert "extra_body" in calls[0] and "extra_body" not in calls[1]
