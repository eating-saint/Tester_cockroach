"""4b 选编号积木：抽列表、读详情字段。4b 只回编号，程序按编号逐字取原文，保证产出在页面上逐字存在。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..pagestate import build_page_state
from ..vlm.digest import PageDigest, text_lines
from .calc import norm
from .engine import Item
from .web import find_text_element, scroll_into_view, scroll_to_top

TRIES = 3
SYSTEM = "你是网页阅读助手。你看截图和元素清单，按要求只回答编号，用 JSON。不要编造清单里没有的编号。"
LIST_SCHEMA = {"type": "object", "properties": {"numbers": {"type": "array", "items": {"type": "integer"}}},
               "required": ["numbers"]}
FIELDS_SCHEMA = {"type": "object", "properties": {"fields": {"type": "array", "items": {
    "type": "object", "properties": {"name": {"type": "string"}, "no": {"type": ["integer", "string", "null"]}},
    "required": ["name", "no"]}}}, "required": ["fields"]}


class PickFailed(RuntimeError):
    """4b 多次回答都不合格式。"""


def _parse_json(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return json.loads(t)


def _as_no(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        return int(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(?:#|编号)?\s*(\d+)\s*", v)
        if m:
            return int(m.group(1))
    return None


async def _ask(ctx, prompt: str, values: dict, images: list[bytes], schema: dict, parse, calls: list):
    """问 4b，回答不合格式就带上错误重问，最多 TRIES 次。parse(obj) 返回结果或抛 ValueError。"""
    last_error = ""
    for _ in range(TRIES):
        user = ctx.prompts.render(prompt, **values, last_error=last_error)
        r = await ctx.client.chat(SYSTEM, user, images, schema)
        calls.append({"raw": r.text, "ms": r.elapsed_ms})
        try:
            return parse(_parse_json(r.text))
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            last_error = f"\n【上一次的问题】你上一次的回答不合格式（{type(e).__name__}: {e}），请只回答要求的 JSON。"
    raise PickFailed(f"4b 连续 {TRIES} 次回答都不合格式")


def _settings(ctx, offscreen: int) -> dict:
    return {**(ctx.vlm or {}), "offscreen_top_k": offscreen}


def _png(ctx, name: str) -> bytes:
    return (Path(ctx.node_dir) / name).read_bytes()


def _label(e) -> str:
    return (e.name or e.text or "").strip()


# ---------------- 抽列表 ----------------
async def _pick_screen(ctx, region: str, anchor_y: float | None, rec: dict, screen: int):
    """看一屏：返回 (条目, 选号记录, 被拒记录, state)。"""
    name = ctx.shot_name()
    state = await build_page_state(ctx.page, ctx.node_dir, shot_name=name)
    rec.setdefault("shots", []).append(name)
    digest = PageDigest.build(state, _png(ctx, name), None, region, _settings(ctx, 0))
    calls: list = []

    def parse(obj):
        nums = obj["numbers"]
        if not isinstance(nums, list):
            raise ValueError("numbers 要是列表")
        return nums
    nums = await _ask(ctx, "pick_list", {"region": region, "elements": digest.element_lines()},
                      [digest.annotated_png], LIST_SCHEMA, parse, calls)
    rec.setdefault("calls", []).extend(calls)
    items, rejected = [], []
    for raw in nums:
        no = _as_no(raw)
        eid = digest.to_id(no) if no is not None else None
        e = state.element(eid) if eid else None
        if e is None:
            rejected.append({"no": raw, "screen": screen, "why": "编号不存在"})
            continue
        if anchor_y is not None and e.box.y + state.viewport.y < anchor_y - 2:
            rejected.append({"no": no, "screen": screen, "why": "在锚点之前", "text": _label(e)})
            continue
        text = _label(e)
        if not text:
            rejected.append({"no": no, "screen": screen, "why": "元素没有文字"})
            continue
        items.append(Item(text, e.id, state.url, e.context or "", origin={"screen": screen, "no": no}))
    return items, [_as_no(x) for x in nums], rejected, state


def split_contexts(items: list[Item]) -> None:
    """同一块里的多个条目（页面上"所在块"是整个列表时）：每条的摘要只取从它自己的文字到下一条文字之前的那一段。
    例：结果列表 600 字以内时每个标题链接的所在块都是整个列表，不切开的话数比例会把别的条目也算进来。"""
    texts = [it.text for it in items]
    for it in items:
        c = it.context or ""
        start = c.find(it.text)
        if start < 0:
            continue
        ends = [c.find(t, start + len(it.text)) for t in texts if t != it.text]
        ends = [e for e in ends if e > start]
        if ends:
            it.origin["context_cut"] = True
            it.context = c[start:min(ends)].strip()
        elif start > 0 and any(t != it.text and t in c[:start] for t in texts):
            it.origin["context_cut"] = True
            it.context = c[start:].strip()


async def pick_list(ctx, p, lists, rec, run, step):
    region = str(p["region"])
    limit = int(p.get("max", 10))
    screens = int(p.get("screens", 6))
    anchor_y = None
    anchors = [str(a) for a in (p.get("anchor") or []) if str(a).strip()]
    if anchors:
        state = await build_page_state(ctx.page)
        el = None
        for a in anchors:
            el = find_text_element(state, a)
            if el is not None:
                rec["anchor"] = a
                break
        if el is None:
            rec["note"] = f"没找到锚点（{' / '.join(anchors)}），不按锚点过滤"
            await scroll_to_top(ctx)
        else:
            await scroll_into_view(ctx, state, el)
            anchor_y = el.box.y + state.viewport.y
    else:
        await scroll_to_top(ctx)                  # 没有锚点：从页面顶部开始看
    out: list[Item] = []
    seen: set[str] = set()
    picked, rejected = [], []
    ex = ctx.executor()
    for screen in range(1, screens + 1):
        items, nums, rej, state = await _pick_screen(ctx, region, anchor_y, rec, screen)
        picked.append({"screen": screen, "numbers": nums})
        rejected.extend(rej)
        new = 0
        for it in items:
            k = norm(it.text)
            if k in seen:
                rejected.append({"no": it.origin.get("no"), "screen": screen, "why": "重复", "text": it.text})
                continue
            seen.add(k)
            it.origin["step"] = rec["step"]
            out.append(it)
            new += 1
        if len(out) >= limit:
            break
        if out and not items:                     # 已经找到过条目、这一屏却没有：区块结束
            break
        if screen == screens:
            break
        before = state.viewport.y
        r = await ex.scroll("down", (state.viewport.height or 800) * 0.8)
        after = float((r.data or {}).get("scroll_y", before))
        if after <= before + 1:                   # 到底了
            break
    rec["picked"], rec["rejected"] = picked, rejected
    split_contexts(out)
    return out[:limit]


# ---------------- 读详情字段 ----------------
async def read_fields(ctx, p, lists, rec, run, step):
    names = [str(n) for n in p.get("names") or []]
    name = ctx.shot_name()
    state = await build_page_state(ctx.page, ctx.node_dir, shot_name=name)
    rec.setdefault("shots", []).append(name)
    digest = PageDigest.build(state, _png(ctx, name), None, " ".join(names), _settings(ctx, 40))
    lines = text_lines(state.body_text)
    calls: list = []

    def parse(obj):
        rows = obj["fields"]
        if not isinstance(rows, list):
            raise ValueError("fields 要是列表")
        return {str(r.get("name")): r.get("no") for r in rows if isinstance(r, dict)}
    got = await _ask(ctx, "pick_fields", {
        "elements": digest.element_lines(), "fields": "、".join(names),
        "lines": "\n".join(f"T{i} {s}" for i, s in enumerate(lines, 1)) or "（没有正文）"},
        [digest.annotated_png], FIELDS_SCHEMA, parse, calls)
    rec["calls"] = calls
    fields: dict[str, Item] = {}
    missing, picked = [], {}
    for f in names:
        no = got.get(f)
        picked[f] = no
        val = None
        if isinstance(no, str) and re.fullmatch(r"\s*[Tt]\s*(\d+)\s*", no):
            i = int(re.findall(r"\d+", no)[0])
            if 1 <= i <= len(lines):
                val = Item(lines[i - 1], f"T{i}", state.url)
        elif no is not None:
            n = _as_no(no)
            eid = digest.to_id(n) if n is not None else None
            e = state.element(eid) if eid else None
            if e is not None and _label(e):
                val = Item(_label(e), e.id, state.url, e.context or "")
        if val is None:
            missing.append(f)
        else:
            fields[f] = val
    rec["picked"], rec["missing"] = picked, missing
    if missing:
        rec["note"] = "未找到：" + "、".join(missing)
    nf = p.get("name_field")
    title = fields[nf].text if nf and nf in fields else (state.title or state.url)
    return [Item(title, fields[nf].element if nf and nf in fields else None, state.url,
                 fields=fields, origin={"step": rec["step"]})]
