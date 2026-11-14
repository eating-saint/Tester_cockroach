"""Playwright 驱动的元素读取：Playwright 无障碍快照给角色、名字、元素引用；每个元素再读一次补充字段与精确坐标。

快照覆盖主页面、同源与跨源 iframe、开放 Shadow DOM；封闭 Shadow DOM 由浏览器启动时注入的脚本改成开放（见 pw/browser.py）。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from ..dom import INTERACTIVE_ROLES, Element, _collapse
from ..geometry import Box

ROLES = INTERACTIVE_ROLES | {"iframe"}

_LINE = re.compile(r'^(\s*)- (.*)$')
_HEAD = re.compile(r'^([A-Za-z]+)(?: "((?:[^"\\]|\\.)*)")?((?: \[[^\]]*\])*)')
_ATTR = re.compile(r'\[([^\]=]+)(?:=([^\]]*))?\]')
_REF = re.compile(r'^(.*?)(e\d+)$')


@dataclass
class Entry:
    role: str
    name: str
    ref: str
    frame_key: str
    disabled: bool
    depth: int


def _unquote_key(s: str) -> str:
    """YAML 里整个键可能被引号包住（名字里含冒号等字符时）。"""
    s = s.rstrip()
    if s.endswith(":"):
        s = s[:-1].rstrip()
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    return s


def parse_snapshot(text: str) -> list[Entry]:
    out: list[Entry] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        body = m.group(2)
        # "- role "name" [attrs]: 文字" —— 冒号后面的文字不属于键；键可能整体加了引号
        if body[:1] in ("'", '"'):
            q = body[0]
            end = body.find(q + ":", 1)
            key = _unquote_key(body[: end + 1] if end > 0 else body)
        else:
            key = body
        h = _HEAD.match(key)
        if not h:
            continue
        role, raw_name, attrs = h.group(1), h.group(2), h.group(3) or ""
        name = ""
        if raw_name is not None:
            try:
                name = json.loads('"' + raw_name + '"')
            except ValueError:
                name = raw_name
        a = {k.strip(): (v if v is not None else True) for k, v in _ATTR.findall(attrs)} if attrs else {}
        ref = a.get("ref")
        if not isinstance(ref, str):
            continue
        rm = _REF.match(ref)
        out.append(Entry(role=role, name=name, ref=ref, frame_key=rm.group(1) if rm else "",
                         disabled="disabled" in a, depth=len(m.group(1)) // 2))
    return out


# 每个元素一次：补充字段 + frame 内精确坐标。context 的算法与 CDP 驱动的 _Doc.context_of 相同
EXTRA_JS = r"""e => {
  const collapse = s => (s || '').split(/\s+/).filter(Boolean).join(' ');
  const textOf = (node, limit) => {
    const out = []; let n = 0; const stack = [node];
    while (stack.length && n < limit) {
      const j = stack.pop();
      if (j.nodeType === 3) { out.push(j.nodeValue); n += j.nodeValue.length; continue; }
      if (j.nodeName === 'SCRIPT' || j.nodeName === 'STYLE') continue;
      const kids = [];
      if (j.shadowRoot) kids.push(...j.shadowRoot.childNodes);
      kids.push(...j.childNodes);
      for (let k = kids.length - 1; k >= 0; k--) stack.push(kids[k]);
    }
    return collapse(out.join(' ')).slice(0, limit);
  };
  const parentOf = n => n instanceof ShadowRoot ? n.host : n.parentNode;
  const tag = e.tagName.toLowerCase();
  const text = textOf(e, 300);
  let best = text, j = e.parentNode;
  for (let k = 0; k < 6; k++) {
    if (!j || ['BODY', 'HTML', '#document'].includes(j.nodeName)) break;
    const t = textOf(j, 601);
    if (t.length > 600) break;
    if (t.length > best.length) best = t;
    j = parentOf(j);              // Shadow 根本身算一层（与 DOMSnapshot 的父子关系一致），再往上是宿主元素
  }
  const r = e.getBoundingClientRect();
  const st = getComputedStyle(e);
  return {
    tag, text, context: best,
    href: tag === 'a' ? (e.getAttribute('href') === null ? null : e.href) : (tag === 'iframe' ? e.getAttribute('src') : null),
    value: ('value' in e && ['input', 'textarea', 'select'].includes(tag)) ? String(e.value) : null,
    disabled: e.disabled === true || e.hasAttribute('disabled'),
    aria_label: e.getAttribute('aria-label'), placeholder: e.getAttribute('placeholder'),
    title: e.getAttribute('title'), name_attr: e.getAttribute('name'),
    in_shadow: e.getRootNode() instanceof ShadowRoot,
    hidden: ['hidden', 'collapse'].includes(st.visibility),
    rect: [r.x, r.y, r.width, r.height], scroll: [scrollX, scrollY],
  };
}"""


class PwDomReader:
    async def read(self, page) -> tuple[list[Element], str]:
        from .page import PwRef

        p = page.p
        lay = await page.layout()
        viewport = Box(0, 0, lay["vw"], lay["vh"])
        snap = await p.aria_snapshot(mode="ai", boxes=True)
        entries = [e for e in parse_snapshot(snap) if e.role in ROLES]

        async def extra(en: Entry):
            try:
                return await p.locator(f"aria-ref={en.ref}").evaluate(EXTRA_JS, timeout=5000)
            except Exception:
                return None

        extras = await asyncio.gather(*[extra(en) for en in entries])
        offsets = await self._offsets(p, entries, extras)
        elements: list[Element] = []
        for en, x in zip(entries, extras):
            if x is None:
                continue
            ox, oy = offsets.get(en.frame_key, (0.0, 0.0))
            rx, ry, rw, rh = x["rect"]
            sx, sy = x["scroll"]
            box = Box(rx + ox, ry + oy, rw, rh)
            visible = (not x["hidden"] and rw > 0 and rh > 0 and rx + sx + rw > 0 and ry + sy + rh > 0)
            tag = x["tag"]
            text = x["text"]
            if tag == "iframe":
                name = x["title"] or x["name_attr"] or ""
            else:
                name = (en.name or x["aria_label"] or text or x["placeholder"] or x["title"]
                        or x["name_attr"] or (x["value"] or ""))
            elements.append(Element(
                id="", role=en.role, name=_collapse(name)[:200], text=text, tag=tag, href=x["href"],
                value=x["value"], box=box, visible=visible, in_viewport=visible and box.intersects(viewport),
                enabled=not (en.disabled or x["disabled"]),
                frame_path=[en.frame_key] if en.frame_key else [], in_shadow=bool(x["in_shadow"]),
                _ref=PwRef(en.frame_key, en.ref),
                context=x["context"] if visible and tag != "iframe" else text))
        for k, e in enumerate(elements):
            e.id = f"e{k}"
        return elements, await self._body_text(p)

    async def _offsets(self, p, entries, extras) -> dict[str, tuple[float, float]]:
        """每个 iframe 前缀对应的坐标偏移：找该 frame 内任一元素所属的 frame，再逐层累加 frame 内容区位置。"""
        out: dict[str, tuple[float, float]] = {"": (0.0, 0.0)}
        for en, x in zip(entries, extras):
            if en.frame_key in out or x is None:
                continue
            try:
                h = await p.locator(f"aria-ref={en.ref}").element_handle(timeout=5000)
                frame = await h.owner_frame()
                await h.dispose()
                out[en.frame_key] = await self.frame_offset(frame)
            except Exception:
                continue
        return out

    @staticmethod
    async def frame_offset(frame) -> tuple[float, float]:
        """frame 内容区左上角在主页面视口中的位置：逐层取 <iframe> 在父 frame 中的 getBoundingClientRect
        加边框与内边距再累加。不用 Playwright 的 bounding_box：它对跨进程 iframe 内的元素少算了 iframe 的边框和内边距。"""
        ox = oy = 0.0
        while frame is not None and frame.parent_frame is not None:
            fe = await frame.frame_element()
            x, y = await fe.evaluate(
                "f => { const r = f.getBoundingClientRect(), s = getComputedStyle(f);"
                " return [r.x + f.clientLeft + parseFloat(s.paddingLeft), r.y + f.clientTop + parseFloat(s.paddingTop)]; }")
            await fe.dispose()
            ox, oy = ox + x, oy + y
            frame = frame.parent_frame
        return ox, oy

    @staticmethod
    async def _body_text(p) -> str:
        texts = []
        for f in p.frames:
            try:
                t = await f.evaluate("document.body ? document.body.innerText : ''")
            except Exception:
                continue
            if t:
                texts.append(t)
        return "\n".join(texts)
