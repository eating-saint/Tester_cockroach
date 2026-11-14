"""面板验收公共设施：进程内起面板后台（随机端口、令牌），REST / WS 客户端，等待状态条件。"""
from __future__ import annotations

import asyncio
import json
import struct
import time
from pathlib import Path

import aiohttp

from hands.config import PROJECT_ROOT


class PanelCtx:
    def __init__(self, driver: str | None = None):
        self.driver = driver
        self.svc = None
        self.runner = None
        self.port = None
        self.token = None
        self.http: aiohttp.ClientSession | None = None
        self.run_root = None

    async def start(self):
        from panel.server import serve
        from panel.service import PanelService
        self.svc = PanelService()
        if self.driver:
            self.svc.config.driver = self.driver
        self.runner, self.port, self.token = await serve(self.svc, 0)
        self.http = aiohttp.ClientSession(headers={"X-Panel-Token": self.token})
        suffix = "" if self.svc.config.driver == "cdp" else f"-{self.svc.config.driver}"
        self.run_root = self.svc.config.runs_dir / (time.strftime("%Y%m%d-%H%M%S") + "-panel-acceptance" + suffix)
        self.run_root.mkdir(parents=True, exist_ok=True)

    async def close(self):
        if self.http:
            await self.http.close()
        if self.svc:
            await self.svc.shutdown()
        if self.runner:
            await self.runner.cleanup()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def artifact(self, name: str) -> Path:
        p = self.run_root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def req(self, method: str, path: str, body=None, expect_ok: bool = True, headers=None):
        async with self.http.request(method, self.base + path, json=body, headers=headers or {}) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {"text": await r.text()}
            if expect_ok and (r.status != 200 or (isinstance(data, dict) and data.get("ok") is False)):
                raise AssertionError(f"{method} {path} -> {r.status} {data}")
            return r.status, data

    async def get(self, path):
        return (await self.req("GET", path))[1]

    async def post(self, path, body=None):
        return (await self.req("POST", path, body or {}))[1]

    async def state(self) -> dict:
        return await self.get("/api/state")

    async def wait_state(self, pred, timeout=30, interval=0.2) -> dict:
        deadline = time.monotonic() + timeout
        st = None
        while time.monotonic() < deadline:
            st = await self.state()
            if pred(st):
                return st
            await asyncio.sleep(interval)
        raise TimeoutError(f"状态条件未满足，最后状态 browser={st and st['browser']} job={st and st['job']}")

    async def wait_job(self, timeout=120) -> dict:
        st = await self.wait_state(lambda s: not s["busy"], timeout)
        return st["job"]

    async def start_job(self, kind, params=None, expect_ok=True):
        return await self.req("POST", "/api/jobs", {"kind": kind, "params": params or {}}, expect_ok=expect_ok)

    def fixture(self, path: str) -> str:
        return f"http://127.0.0.1:8765/{path.lstrip('/')}"

    async def ws(self):
        return await self.http.ws_connect(f"ws://127.0.0.1:{self.port}/ws?token={self.token}", max_msg_size=0)


def parse_frame(data: bytes) -> tuple[dict, bytes]:
    n = struct.unpack(">I", data[:4])[0]
    return json.loads(data[4:4 + n].decode("utf-8")), data[4 + n:]


def load_expected(fixture: str) -> dict:
    return json.loads((PROJECT_ROOT / "fixtures" / fixture / "expected.json").read_text(encoding="utf-8"))
