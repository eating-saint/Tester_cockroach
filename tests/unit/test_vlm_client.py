"""替身模型：按顺序回答、记录请求、可设为不可用；OllamaClient 连不上时给中文提示。"""
import asyncio

import pytest

from hands.vlm.client import ModelUnavailable, OllamaClient, ScriptedClient


def test_scripted_order_and_calls():
    c = ScriptedClient(["a", "b"])
    assert asyncio.run(c.chat("s", "u1", [b"x"], None)).text == "a"
    assert asyncio.run(c.chat("s", "u2", [], {"type": "object"})).text == "b"
    assert [x["user"] for x in c.calls] == ["u1", "u2"]
    assert c.calls[0]["images"] == 1 and c.calls[1]["schema"] == {"type": "object"}


def test_scripted_raises_preset_exception():
    c = ScriptedClient([ModelUnavailable("x")])
    with pytest.raises(ModelUnavailable):
        asyncio.run(c.chat("s", "u", [], None))


def test_scripted_unavailable():
    with pytest.raises(ModelUnavailable, match="没有启动"):
        asyncio.run(ScriptedClient(available=False).check())


def test_ollama_down_message():
    from hands.config import VLM_DEFAULTS
    c = OllamaClient({**VLM_DEFAULTS, "base_url": "http://127.0.0.1:9"})   # 端口 9 无人监听
    with pytest.raises(ModelUnavailable, match="模型服务没有启动"):
        asyncio.run(c.check())
