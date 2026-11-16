"""点餐类结果：价位解析与排序（spec F16）。

价位统一成 1–4 档：$～$$$$ 直接对应；金额（人均 ¥45、$12–18、¥30-60）取中值换算成美元，
按档位边界（默认 ≤15、≤30、≤60 美元，其余为 4 档）归档。识别不了的为 None（价位未知，排在已知之后）。
"""
from __future__ import annotations

import re

DEFAULT_BOUNDS = (15.0, 30.0, 60.0)
DEFAULT_CNY_PER_USD = 7.2


def parse_price(s: str | None, bounds=DEFAULT_BOUNDS, cny_per_usd: float = DEFAULT_CNY_PER_USD) -> int | None:
    if not s:
        return None
    t = str(s).strip()
    m = re.fullmatch(r"\s*(?:价位[:：]?\s*)?([$￥¥])\1{0,3}\s*", t)
    if m and not re.search(r"\d", t):
        return min(len(t.replace("价位", "").replace(":", "").replace("：", "").strip()), 4)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return None
    amount = sum(nums[:2]) / min(len(nums), 2)
    if "¥" in t or "￥" in t or "元" in t or "人均" in t and "$" not in t:
        amount /= cny_per_usd
    for i, b in enumerate(bounds, 1):
        if amount <= b:
            return i
    return len(bounds) + 1


def parse_rating(s) -> float | None:
    if s is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(s))
    return float(m.group()) if m else None


def _key(item: dict, sort: list[str]):
    out = []
    for spec in sort:
        field, _, order = spec.partition(":")
        v = item.get(field)
        missing = v is None
        if not missing and order == "desc":
            v = -v
        out.append((missing, v if not missing else 0))
    return tuple(out)


def rank(items: list[dict], sort: list[str] | None = None, n: int = 5, group_by: str | None = None,
         bounds=DEFAULT_BOUNDS, cny_per_usd: float = DEFAULT_CNY_PER_USD) -> dict:
    """items：[{name, price_text, rating_text, grounded, group?, ...}]。
    返回 {"top": {组: [条目…]}, "ungrounded": [...]}；没有 group_by 时组名为 ""。"""
    sort = sort or ["price:asc", "rating:desc"]
    good, bad = [], []
    for it in items:
        row = dict(it)
        row["price"] = parse_price(it.get("price_text"), bounds, cny_per_usd)
        row["rating"] = parse_rating(it.get("rating_text"))
        row["price_known"] = row["price"] is not None
        (good if it.get("grounded") else bad).append(row)
    groups: dict[str, list[dict]] = {}
    for row in good:
        groups.setdefault(str(row.get("group") or "") if group_by else "", []).append(row)
    top = {}
    for g, rows in groups.items():
        seen, uniq = set(), []
        for r in sorted(rows, key=lambda r: _key(r, sort)):
            if r.get("name") in seen:
                continue
            seen.add(r.get("name"))
            uniq.append(r)
        top[g] = uniq[:n]
    return {"top": top, "ungrounded": bad}
