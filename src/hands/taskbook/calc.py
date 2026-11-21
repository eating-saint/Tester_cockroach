"""计算积木（纯函数，同样的输入一定得到同样的输出）：数比例、取名次、过滤、比较字段。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from ..expand.rank import parse_price
from .engine import Item


def norm(s, ignore_case: bool = True) -> str:
    """全角转半角、去掉所有空白；ignore_case 时转小写。"""
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or "")))
    return s.lower() if ignore_case else s


def _texts(words) -> list[str]:
    return [w.text if isinstance(w, Item) else str(w) for w in (words or [])]


# ---------------- 数比例 ----------------
def ratio(items: list[Item], top: int, words, at_least: float, current: str = "",
          drop_current: bool = True) -> tuple[list[Item], list[dict]]:
    """words 里每个词：数 items 前 top 条里 text 或 context 包含它的条数，比例 ≥ at_least 的输出。"""
    head = list(items or [])[:max(int(top), 0)]
    log: list[dict] = []
    out: list[Item] = []
    if not head:
        return out, [{"why": "结果清单为空"}]
    hay = [norm(f"{it.text} {it.context}") for it in head]
    seen = set()
    for w in _texts(words):
        nw = norm(w)
        if not nw or nw in seen:
            continue
        seen.add(nw)
        if drop_current and nw == norm(current):
            continue
        hits = [i + 1 for i, h in enumerate(hay) if nw in h]
        r = len(hits) / len(head)
        log.append({"word": w, "ratio": round(r, 4), "hits": hits, "of": len(head)})
        if r >= at_least - 1e-9:
            out.append(Item(w, origin={"ratio": round(r, 4), "hits": hits, "of": len(head)}))
    return out, log


# ---------------- 取名次 ----------------
def rank(items: list[Item], k: int | None = None, top_k: int | None = None) -> tuple[list[Item], list[dict]]:
    items = list(items or [])
    if k is not None:
        if 1 <= k <= len(items):
            return [items[k - 1]], []
        return [], [{"why": f"只有 {len(items)} 条，没有第 {k} 名"}]
    n = int(top_k or 0)
    log = [] if len(items) >= n else [{"why": f"只有 {len(items)} 条，不足 {n} 个"}]
    return items[:n], log


# ---------------- 过滤 ----------------
def filter_items(items: list[Item], alias: dict | None = None, stop=None, current: str = "", known=None,
                 drop_current: bool = False, drop_known: bool = False, only_known: bool = False,
                 ignore_case: bool = True, contains: bool = False) -> tuple[list[Item], list[dict]]:
    """依次：别名归一 → 停用词 → 去当前词 → 去已知词 / 只留已知词 → 去重。返回 (留下的, 滤掉的 [{text, reason}])。"""
    n = (lambda s: norm(s, ignore_case))

    def match(a: str, b: str) -> bool:          # a 是条目文字，b 是词
        na, nb = n(a), n(b)
        return bool(nb) and (nb in na if contains else na == nb)
    aliases = [(str(k), str(v)) for k, v in (alias or {}).items()]
    stops = [s for s in _texts(stop) if n(s)]
    known_words = _texts(known)
    out, dropped, seen = [], [], set()
    for it in items or []:
        cur = it
        for a, canon in aliases:
            if match(cur.text, a):
                cur = replace(it, text=canon, origin={**it.origin, "alias_from": it.text})
                break
        hit_stop = next((s for s in stops if n(s) in n(cur.text)), None)
        if hit_stop is not None:
            dropped.append({"text": it.text, "reason": "停用词", "word": hit_stop})
            continue
        if drop_current and current and match(cur.text, current):
            reason = "别名归一后是当前词" if cur is not it else "当前词"
            dropped.append({"text": it.text, "reason": reason, "word": current})
            continue
        hit_known = next((w for w in known_words if match(cur.text, w)), None)
        if drop_known and hit_known is not None:
            dropped.append({"text": it.text, "reason": "已有词", "word": hit_known})
            continue
        if only_known:
            if hit_known is None:
                dropped.append({"text": it.text, "reason": "不在已知词"})
                continue
            if n(cur.text) != n(hit_known):
                cur = replace(cur, text=hit_known, origin={**cur.origin, "matched_from": it.text})
        key = n(cur.text)
        if key in seen:
            dropped.append({"text": it.text, "reason": "重复"})
            continue
        seen.add(key)
        out.append(cur)
    return out, dropped


# ---------------- 比较字段 ----------------
def _is_price(field: str, value: str) -> bool:
    return "价" in field or "price" in field.lower() or bool(re.fullmatch(r"\s*[$¥￥]{1,4}\s*", str(value)))


def _number(s) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or "").replace(",", ""))
    return float(m.group()) if m else None


def compare(items: list[Item], field: str, op: str, value) -> tuple[list[Item], list[Item], list[dict]]:
    """返回 (满足的, 识别不了的, 不满足的 [{text, reason}])。比较值解析不了抛 ValueError。"""
    value = str(value)
    price = _is_price(field, value)
    if op == "contains":
        target = norm(value)
        if not target:
            raise ValueError("比较值为空")
    else:
        target = parse_price(value) if price else _number(value)
        if target is None:
            raise ValueError(f"比较值「{value}」识别不了")
    ok, unknown, dropped = [], [], []
    for it in items or []:
        f = it.fields.get(field)
        raw = f.text if isinstance(f, Item) else (str(f) if f is not None else None)
        if raw is None or raw == "":
            unknown.append(it)
            dropped.append({"text": it.text, "reason": "未知", "field": field})
            continue
        if op == "contains":
            good = target in norm(raw)
        else:
            v = parse_price(raw) if price else _number(raw)
            if v is None:
                unknown.append(it)
                dropped.append({"text": it.text, "reason": "未知", "field": field, "value": raw})
                continue
            good = v <= target if op == "<=" else v >= target if op == ">=" else v == target
        if good:
            ok.append(it)
        else:
            dropped.append({"text": it.text, "reason": "比较不满足", "field": field, "value": raw})
    return ok, unknown, dropped
