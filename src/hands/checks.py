"""转人工检查与按域名访问间隔：CaseRunner 与任务书积木共用（行为与 ver0 / 上一章一致）。

转人工的情况：验证码（网址 / 正文 / iframe 命中）、登录 / 密码 / 短信验证（需确认开启时）、
上一步动作要求转人工（如目标是密码框）、系统对话框（打印、保存）。
同一个验证框人工处理过后 iframe 还留在页面上，不重复转人工；登录页同理。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import urlparse

from . import captcha

MAX_HANDOFFS = 3          # 同一处连续转人工超过这么多次仍未解决：判失败


class HandoffFailed(Exception):
    """转人工多次后仍未解决。"""


class HandoffChecker:
    def __init__(self, browser, config, confirm, gate, handoff_fn=None, input_fn=input, log=print,
                 grace: float = 0.0):
        self.browser = browser
        self.config = config
        self.confirm = confirm                 # ConfirmPolicy（login_hit）
        self.gate = gate
        self.handoff_fn = handoff_fn           # None = captcha.handoff（切有界面、等回车）
        self.input_fn = input_fn
        self.log = log
        self.grace = grace                     # 人工处理完后的宽限期（桌面驱动）
        self.handled: set = set()              # 已处理过的签名
        self.pending: str | None = None        # 上一步动作要求转人工
        self.records: list[dict] = []          # [{start, end, hit, url}]

    def set_pending(self, hit: str):
        self.pending = hit

    async def detect(self, state, page) -> str | None:
        """返回需要转人工的原因（如 "captcha:url"、"login:password"、"dialog:打印"），没有则 None。"""
        hit = captcha.detect(state, self.config.captcha_markers)
        if hit and hit.startswith("iframe:") and captcha.signature(state, hit) in self.handled:
            # 同一个验证框已经人工处理过：真实验证框通过后 iframe 还留在页面上，不重复转人工。
            # 网址或正文命中（拦截页本身）还在，说明没通过：照常再转人工。
            hit = None
        if not hit and self.confirm is not None and self.confirm.enabled:
            login = self.confirm.login_hit(state)
            if login and captcha.signature(state, f"login:{login}") not in self.handled:
                hit = f"login:{login}"
        if not hit and self.pending:
            hit, self.pending = self.pending, None
        if not hit:
            dlg = getattr(page, "dialogs", None)
            if dlg is not None:
                found = await dlg()
                if found:                      # 系统对话框 → 转人工，不去点它
                    hit = f"dialog:{found[0].get('title') or found[0].get('hwnd')}"
        return hit

    async def handle(self, hit: str, state, save_dir) -> tuple[object, dict]:
        """暂停、交给人处理、恢复。返回 (处理后的页面, 记录)。"""
        h_start = time.time()
        self.gate.pause("handoff", {"hit": hit})          # 人工处理期间的鼠标键盘不算"抢占"
        try:
            if self.handoff_fn is None:
                page = await captcha.handoff(self.browser, state.url, save_dir, self.input_fn, self.log,
                                             markers=self.config.captcha_markers)
            else:
                page = await self.handoff_fn(self.browser, state.url, save_dir, self.input_fn, self.log)
        finally:
            self.gate.resume("handoff")
            self.gate.arm(self.grace)
        rec = {"start": h_start, "end": time.time(), "hit": hit, "url": state.url}
        self.records.append(rec)
        self.handled.add(captcha.signature(state, hit))
        return page, rec

    async def check_page(self, page, save_dir, state=None) -> tuple[object, list[dict]]:
        """检查当前页面，需要时转人工，直到不再需要（最多 MAX_HANDOFFS 次，超过抛 HandoffFailed）。
        返回 (页面, 本次的转人工记录)。state 给出时第一次直接用它，之后重新读取页面。"""
        from .pagestate import build_page_state
        save_dir = Path(save_dir)
        recs: list[dict] = []
        for i in range(MAX_HANDOFFS + 1):
            if i > 0 or state is None:
                d = save_dir / f"check{i}"
                d.mkdir(parents=True, exist_ok=True)
                state = await build_page_state(page, d)
            hit = await self.detect(state, page)
            if not hit:
                return page, recs
            self.log(f"[HUMAN_REQUIRED] 检测到 {hit}")
            if i >= MAX_HANDOFFS:
                raise HandoffFailed(f"captcha not resolved: {hit}")
            page, rec = await self.handle(hit, state, save_dir)
            recs.append(rec)
        return page, recs


async def respect_interval(last_visit: dict, url: str, min_interval: dict | None, log=print):
    """按域名最小访问间隔：距上次访问同一域名不足 gap 秒就等待。last_visit 由调用方持有。"""
    host = (urlparse(url).hostname or "").lower()
    for domain, gap in (min_interval or {}).items():
        if host == domain or host.endswith("." + domain):
            last = last_visit.get(domain)
            if last is not None:
                wait = gap - (time.monotonic() - last)
                if wait > 0:
                    log(f"[interval] {domain}: 距上次访问不足 {gap}s，等待 {wait:.1f}s")
                    await asyncio.sleep(wait)
            last_visit[domain] = time.monotonic()
