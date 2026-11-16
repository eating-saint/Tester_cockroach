"""需确认策略（spec F9）：不硬拦、但要先暂停等用户"允许一次 / 拒绝"的动作，以及登录类转人工。

- 下单、支付仍由 guard.py 硬拦截，先于本模块判断，不能放行。
- enabled 为 None 时按驱动能力默认：桌面驱动开，CDP / Playwright 关（现有行为不变）。
- 英文词按词边界匹配（避免 "book" 命中 "Facebook"），中文按子串匹配。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse

ACCOUNT_WORDS = (
    "加入购物车", "加购", "加入收藏", "收藏", "关注", "点赞", "写评价", "写点评", "发表评价", "发消息", "私信",
    "预订", "预约", "订座", "立即预订",
    "add to cart", "add to bag", "save", "follow", "like", "write a review", "write review",
    "message", "send message", "book", "reserve", "add to favorites", "add to wishlist",
)
SUBMIT_WORDS = (
    "提交", "保存", "确认", "继续", "下一步", "完成",
    "submit", "save changes", "continue", "next", "confirm", "done",
)
LOGIN_WORDS = ("登录", "登入", "注册", "验证码", "短信验证", "sign in", "log in", "login", "sign up",
               "verification code", "one-time code", "otp")
LOGIN_URL_PARTS = ("login", "signin", "sign-in", "sign_in", "passport", "/auth", "account/verify")
SEARCH_HINTS = ("search", "搜索", "查找", "find", "query", "关键词")
ACTION_ROLES = {"button", "link", "menuitem", "checkbox", "switch", "tab"}


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _has(text: str, word: str) -> bool:
    if not text or not word:
        return False
    if word.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


@dataclass
class ConfirmRequest:
    kind: str               # account_change / form_submit / unverifiable_point
    tool: str
    target: dict            # 角色、名称、文字、落点
    hit: str                # 命中的词或规则

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        kinds = {"account_change": "会改账号状态的操作", "form_submit": "提交表单",
                 "unverifiable_point": "网格落点无法核对"}
        name = self.target.get("name") or self.target.get("text") or ""
        return f"{kinds.get(self.kind, self.kind)}：{self.tool}「{name}」，命中「{self.hit}」"


ConfirmFn = Callable[[ConfirmRequest], Awaitable[bool]]


def _target(el, extra: dict | None = None) -> dict:
    d = {"role": getattr(el, "role", None), "name": (getattr(el, "name", "") or "")[:80],
         "text": (getattr(el, "text", "") or "")[:80], "source": getattr(el, "source", "dom")}
    d.update(extra or {})
    return d


@dataclass
class ConfirmPolicy:
    enabled: bool = False
    account_words: list[str] = field(default_factory=lambda: list(ACCOUNT_WORDS))
    submit_words: list[str] = field(default_factory=lambda: list(SUBMIT_WORDS))
    login_words: list[str] = field(default_factory=lambda: list(LOGIN_WORDS))

    @classmethod
    def from_config(cls, cfg: dict | None, default_on: bool) -> "ConfirmPolicy":
        cfg = cfg or {}
        on = cfg.get("enabled")
        enabled = default_on if on is None else bool(on)

        def group(name: str, base) -> list[str]:
            add = (cfg.get("add_words") or {}).get(name) or []
            rm = {_norm(w) for w in ((cfg.get("remove_words") or {}).get(name) or [])}
            return list(dict.fromkeys(_norm(w) for w in [*base, *add] if _norm(w) and _norm(w) not in rm))
        return cls(enabled, group("account", ACCOUNT_WORDS), group("submit", SUBMIT_WORDS),
                   group("login", LOGIN_WORDS))

    # ---------------- 判断 ----------------
    def check_click(self, el, tool: str = "click") -> ConfirmRequest | None:
        if not self.enabled or el is None:
            return None
        role = getattr(el, "role", "") or ""
        texts = [_norm(getattr(el, "name", "")), _norm(getattr(el, "text", ""))]
        if role in ACTION_ROLES or role == "grid_cell":
            for t in texts:
                for w in self.account_words:
                    if _has(t, w):
                        return ConfirmRequest("account_change", tool, _target(el), w)
        if role == "button":
            name = texts[0] or texts[1]
            if name and not any(h in name for h in SEARCH_HINTS):
                for w in self.submit_words:
                    # 只认"按钮名就是这个词"（允许少量附加字），避免"继续阅读"之类误判太多
                    if _has(name, w) and len(name) <= len(w) + 6:
                        return ConfirmRequest("form_submit", tool, _target(el), w)
        return None

    def check_enter(self, focused) -> ConfirmRequest | None:
        """在非搜索框里按回车 = 提交表单。焦点元素未知时不拦（拿不到依据）。"""
        if not self.enabled or focused is None:
            return None
        role = getattr(focused, "role", "") or ""
        if role in ("searchbox", "combobox"):
            return None
        name = _norm(getattr(focused, "name", "")) + " " + _norm(getattr(focused, "text", ""))
        if any(h in name for h in SEARCH_HINTS):
            return None
        if role == "textbox":
            return ConfirmRequest("form_submit", "press", _target(focused, {"key": "Enter"}), "非搜索框回车")
        return None

    def unverifiable(self, tool: str, x: float, y: float) -> ConfirmRequest | None:
        if not self.enabled:
            return None
        return ConfirmRequest("unverifiable_point", tool, {"role": "grid_cell", "name": "", "x": x, "y": y},
                              "落点查不到元素")

    def login_hit(self, state) -> str | None:
        """登录页、密码框、短信验证码框 → 转人工。返回命中说明。"""
        if not self.enabled or state is None:
            return None
        for e in getattr(state, "elements", []) or []:
            if getattr(e, "is_password", False) and e.visible:
                return f"password:{e.name or e.id}"
        url = (getattr(state, "url", "") or "").lower()
        try:
            u = urlparse(url)
            hay = (u.netloc + u.path).lower()
        except ValueError:
            hay = url
        for part in LOGIN_URL_PARTS:
            if part in hay:
                return f"url:{part}"
        title = _norm(getattr(state, "title", ""))
        for w in ("登录", "登入", "sign in", "log in", "login"):
            if _has(title, w):
                return f"title:{w}"
        for e in getattr(state, "elements", []) or []:
            if e.role == "textbox" and e.visible:
                n = _norm(e.name)
                for w in ("验证码", "短信", "verification code", "one-time code", "otp"):
                    if _has(n, w):
                        return f"code_field:{w}"
        return None


def cli_confirm_fn(input_fn=input, log=print) -> ConfirmFn:
    """命令行确认：打印请求，输入 y 允许一次。"""
    import asyncio

    async def fn(req: ConfirmRequest) -> bool:
        log(f"[需确认] {req.describe()}")
        try:
            ans = await asyncio.to_thread(input_fn, "允许一次？[y/N] ")
        except (EOFError, OSError):
            ans = ""
        return str(ans).strip().lower() in ("y", "yes", "是")
    return fn
