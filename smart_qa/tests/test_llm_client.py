"""统一 LLM 客户端单元测试(不依赖真实 endpoint)。

验证 LLMClient 的 available/status 行为,以及 chat 的 payload 构造
(json_mode 开关)——确保 proposer(json_mode=False)与 parser(json_mode=True)
共用同一入口时 payload 正确。
"""
from __future__ import annotations
import os
import sys
import json
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
sys.path.insert(0, os.path.abspath(_SRC))

import llm_client  # noqa: E402


def test_unavailable_without_config(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    c = llm_client.LLMClient()
    assert not c.available
    assert "未配置" in c.status()


def test_available_with_explicit():
    c = llm_client.LLMClient(base_url="http://x", api_key="k", model="m")
    assert c.available
    assert "http://x" in c.status()
    assert "m" in c.status()


def test_chat_unavailable_raises(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    c = llm_client.LLMClient()
    with pytest.raises(llm_client.LLMUnavailable):
        c.chat("s", "u")


class _FakeResp:
    def __init__(self, content: str):
        self._content = json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    def read(self):
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_chat_payload_json_mode(monkeypatch):
    """monkeypatch urlopen 验证:json_mode=True 时 payload 含 response_format;False 时不含。"""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp("{}")

    monkeypatch.setattr(llm_client.urllib.request, "urlopen", fake_urlopen)
    c = llm_client.LLMClient(base_url="http://x/v1", api_key="k", model="m")

    c.chat("sys", "usr", json_mode=True)
    assert captured["url"] == "http://x/v1/chat/completions"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["model"] == "m"

    c.chat("sys", "usr", json_mode=False)
    assert "response_format" not in captured["body"]


def test_singleton_get_default():
    a = llm_client.get_default()
    b = llm_client.get_default()
    assert a is b
