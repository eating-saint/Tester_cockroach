"""cloudflared 临时隧道（平板远程访问的公网档，spec F10）。

启动 `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<端口>`，从输出里找 https://*.trycloudflare.com。
30 秒内拿不到地址判失败；进程意外退出时回调 on_down（主动 stop 不回调）。
"""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
INSTALL_HINT = "本机没有找到 cloudflared：可以用 winget install Cloudflare.cloudflared 安装，或在 config.yaml 的 remote.cloudflared 写它的路径"


def find_exe(configured: str | None = None):
    """返回可执行文件（路径字符串，或测试用的命令列表）；找不到返回 None。"""
    if isinstance(configured, (list, tuple)):
        return list(configured)
    if configured:
        return configured if Path(configured).exists() else None
    return shutil.which("cloudflared")


class Tunnel:
    def __init__(self, exe, port: int, on_down=None, log=print):
        self.exe = exe
        self.port = port
        self.on_down = on_down
        self.log = log
        self.proc: asyncio.subprocess.Process | None = None
        self.url: str | None = None
        self._stopping = False
        self._watch: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self.lines: list[str] = []

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self, timeout: float = 30) -> str:
        if self.exe is None:
            raise RuntimeError(INSTALL_HINT)
        cmd = (list(self.exe) if isinstance(self.exe, (list, tuple)) else [self.exe]) + \
            ["tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"]
        self._stopping = False
        self.proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.STDOUT)
        got: asyncio.Future = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read(got))
        try:
            self.url = await asyncio.wait_for(asyncio.shield(got), timeout)
        except asyncio.TimeoutError:
            await self.stop()
            raise RuntimeError(f"隧道 {int(timeout)} 秒内没有拿到地址（cloudflared 输出：{' / '.join(self.lines[-3:])}）") from None
        if self.url is None:
            await self.stop()
            raise RuntimeError("cloudflared 没有给出公网地址就退出了：" + " / ".join(self.lines[-3:]))
        self._watch = asyncio.create_task(self._wait_exit())
        return self.url

    async def _read(self, got: asyncio.Future):
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            self.lines = (self.lines + [text])[-20:]
            m = URL_RE.search(text)
            if m and not got.done():
                got.set_result(m.group(0))
        if not got.done():
            got.set_result(None)

    async def _wait_exit(self):
        assert self.proc
        await self.proc.wait()
        if not self._stopping:
            self.log(f"[remote] 隧道进程意外退出（{self.proc.returncode}）")
            self.url = None
            if self.on_down is not None:
                r = self.on_down()
                if asyncio.iscoroutine(r):
                    await r

    async def stop(self):
        self._stopping = True
        p = self.proc
        if p is not None and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), 3)
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()
        for t in (self._watch, self._reader):
            if t is not None and not t.done():
                t.cancel()
        self.url = None
