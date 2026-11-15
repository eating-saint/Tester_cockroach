"""模型调用：OllamaClient（本机 Ollama）与 ScriptedClient（按预设回答的替身，供测试和验收用）。"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Protocol


class ModelUnavailable(RuntimeError):
    """模型服务不可用（没启动、模型没拉取、超时）。消息是给用户看的中文，含处理办法。"""


@dataclass
class ChatResult:
    text: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0


class ModelClient(Protocol):
    model: str

    async def chat(self, system: str, user: str, images: list[bytes], schema: dict | None) -> ChatResult: ...
    async def check(self) -> None: ...
    async def vram(self) -> dict | None: ...


@dataclass
class ScriptedClient:
    """按顺序返回预设回答；元素是字符串（回答文字）或异常（调用时抛出）。记下每次收到的请求。"""
    replies: list = field(default_factory=list)
    available: bool = True
    model: str = "scripted"
    calls: list = field(default_factory=list)

    async def chat(self, system, user, images, schema) -> ChatResult:
        self.calls.append({"system": system, "user": user, "images": len(images), "schema": schema})
        if not self.replies:
            raise AssertionError("ScriptedClient: 预设回答已用完")
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return ChatResult(text=r)

    async def check(self) -> None:
        if not self.available:
            raise ModelUnavailable("模型服务没有启动（替身模型设为不可用）")

    async def vram(self) -> dict | None:
        return None


class OllamaClient:
    def __init__(self, settings: dict):
        self.s = dict(settings)
        self.model = self.s["model"]
        self.base_url = str(self.s["base_url"]).rstrip("/")

    def _http(self, timeout: float | None = None):
        import httpx
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout or float(self.s["timeout_s"]))

    def _down(self) -> ModelUnavailable:
        return ModelUnavailable(f"模型服务没有启动：请打开 Ollama（或检查配置 vlm.base_url = {self.base_url}）")

    async def check(self) -> None:
        import httpx
        try:
            async with self._http(10) as c:
                r = await c.get("/api/tags")
                r.raise_for_status()
                names = {m.get("name") for m in r.json().get("models", [])}
        except (httpx.HTTPError, OSError, ValueError):
            raise self._down() from None
        want = self.model if ":" in self.model else self.model + ":latest"
        if self.model not in names and want not in names:
            raise ModelUnavailable(f"模型 {self.model} 没有拉取：请运行 ollama pull {self.model}")

    async def chat(self, system: str, user: str, images: list[bytes], schema: dict | None) -> ChatResult:
        import httpx
        msg = {"role": "user", "content": user}
        if images:
            msg["images"] = [base64.b64encode(b).decode("ascii") for b in images]
        body = {
            "model": self.model, "stream": False, "think": False, "keep_alive": self.s.get("keep_alive", "10m"),
            "options": {"temperature": self.s["temperature"], "seed": self.s["seed"], "num_ctx": self.s["num_ctx"]},
            "messages": [{"role": "system", "content": system}, msg],
        }
        if schema is not None:
            body["format"] = schema
        t0 = time.monotonic()
        try:
            async with self._http() as c:
                r = await c.post("/api/chat", json=body)
        except httpx.TimeoutException:
            raise ModelUnavailable(f"模型调用超时（{self.s['timeout_s']} 秒）：可调大 vlm.timeout_s，"
                                   f"或用 ollama ps 检查模型是否在显卡上") from None
        except (httpx.HTTPError, OSError):
            raise self._down() from None
        if r.status_code == 404:
            raise ModelUnavailable(f"模型 {self.model} 没有拉取：请运行 ollama pull {self.model}")
        if r.status_code >= 400:
            raise ModelUnavailable(f"模型服务返回错误 {r.status_code}：{r.text[:200]}")
        d = r.json()
        return ChatResult(text=(d.get("message") or {}).get("content", ""),
                          prompt_tokens=int(d.get("prompt_eval_count") or 0),
                          output_tokens=int(d.get("eval_count") or 0),
                          elapsed_ms=int((time.monotonic() - t0) * 1000))

    async def unload(self) -> None:
        """让 Ollama 立刻把模型移出显存（对比显存占用时用）。"""
        import httpx
        try:
            async with self._http(30) as c:
                await c.post("/api/generate", json={"model": self.model, "keep_alive": 0})
        except (httpx.HTTPError, OSError):
            pass

    async def vram(self) -> dict | None:
        import httpx
        try:
            async with self._http(10) as c:
                r = await c.get("/api/ps")
                for m in r.json().get("models", []):
                    if m.get("name") == self.model or m.get("model") == self.model:
                        return {"size": m.get("size"), "size_vram": m.get("size_vram")}
        except (httpx.HTTPError, OSError, ValueError):
            return None
        return None
