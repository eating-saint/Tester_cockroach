"""兜底拦截：动作执行前拦住下单、支付类操作。对规则决策、大模型决策、面板指定目标一律生效。

只能通过配置增删清单，没有整体关闭的开关（spec ver1 F6、N9）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_WORDS: tuple[str, ...] = (
    "place order", "place your order", "checkout", "check out", "pay now", "buy now", "submit order",
    "confirm order", "complete purchase", "complete order",
    "提交订单", "去结算", "立即购买", "确认付款", "立即支付", "确认订单", "确认支付", "去付款", "立即付款",
)
DEFAULT_PATHS: tuple[str, ...] = (
    "/checkout", "/pay", "/payment", "/order/confirm", "/order/submit", "/buy", "/cashier", "/purchase",
)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


@dataclass
class GuardHit:
    kind: str       # word / path
    matched: str    # 命中的词或路径
    where: str      # name / text / context / href / form_action / page_url

    def describe(self) -> str:
        return f"命中「{self.matched}」({self.where})"


class Guard:
    def __init__(self, words=DEFAULT_WORDS, paths=DEFAULT_PATHS):
        self.words = tuple(dict.fromkeys(_norm(w) for w in words if _norm(w)))
        self.paths = tuple(dict.fromkeys(p.strip().lower() for p in paths if p.strip()))

    @classmethod
    def from_config(cls, cfg: dict | None) -> "Guard":
        cfg = cfg or {}
        rm_w = {_norm(w) for w in cfg.get("remove_words") or []}
        rm_p = {p.strip().lower() for p in cfg.get("remove_paths") or []}
        words = [w for w in DEFAULT_WORDS if _norm(w) not in rm_w] + list(cfg.get("add_words") or [])
        paths = [p for p in DEFAULT_PATHS if p.lower() not in rm_p] + list(cfg.get("add_paths") or [])
        return cls(words, paths)

    def word_hit(self, text: str | None, where: str) -> GuardHit | None:
        t = _norm(text)
        if not t:
            return None
        for w in self.words:
            if w in t:
                return GuardHit("word", w, where)
        return None

    def path_hit(self, url: str | None, where: str) -> GuardHit | None:
        if not url:
            return None
        try:
            path = urlparse(url).path.lower()
        except ValueError:
            return None
        for p in self.paths:
            # 路径片段匹配：/checkout 命中 /checkout、/checkout/step1、/cart/checkout，不命中 /checkoutguide
            if re.search(re.escape(p) + r"(?:/|$|\.|\?)", path):
                return GuardHit("path", p, where)
        return None

    def check(self, el, form_action: str | None = None) -> GuardHit | None:
        if el is not None:
            for where in ("name", "text"):
                hit = self.word_hit(getattr(el, where, None), where)
                if hit:
                    return hit
            hit = self.path_hit(getattr(el, "href", None), "href")
            if hit:
                return hit
        return self.path_hit(form_action, "form_action")
