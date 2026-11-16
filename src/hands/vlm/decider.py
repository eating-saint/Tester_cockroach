"""视觉大模型决策器：一步内完成"页面摘要 → 描述（或复用）→ 决策 → 编号换成元素 id"。

实现与规则决策器相同的约定：decide(DecisionInput) -> 决策 JSON 字符串（spec ver1 G4、F3、F4）。
本步与模型有关的记录写进 step_dir/vlm/（F8、N3）。
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from ..config import vlm_settings
from ..decision import tools_for
from .client import OllamaClient
from .describe_cache import DESCRIBE_SCHEMA, REGION_KINDS, POSITIONS, DescribeCache, PageDescription
from .digest import PageDigest
from .prompts import PromptBook

def decide_schema(caps=None) -> dict:
    """工具名枚举按驱动能力过滤：CDP / Playwright 下与之前完全相同。"""
    return {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "tool": {"type": "string", "enum": list(tools_for(caps))},
            "arguments": {"type": "object"},
        },
        "required": ["reason", "tool", "arguments"],
    }


DECIDE_SCHEMA = decide_schema(None)
DESCRIBE_TRIES = 3          # 描述输出不合格式：首次 + 重试 2 次


def _blank_png() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(out, format="PNG")
    return out.getvalue()


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _parse_json(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return json.loads(t)


def _as_no(v):
    """模型写的编号：7、"7"、"#7"、"编号7" 都认；认不出返回 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        return int(v)
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(?:#|编号)?\s*(\d+)\s*", v)
        if m:
            return int(m.group(1))
    return None


def _explain_error(err: str) -> str:
    m = re.match(r"unknown element #(\d+)", err or "")
    if m:
        return f"编号 {m.group(1)} 不在本页的元素清单里，只能用清单里的编号"
    return err


class VlmDecider:
    def __init__(self, config, client=None, prompts: PromptBook | None = None):
        self.s = vlm_settings(config)
        self.client = client if client is not None else OllamaClient(self.s)
        self.pb = prompts or PromptBook(self.s.get("prompts_dir"))
        self.cache = DescribeCache(self.s)
        self._step: dict = {}          # 当前步：{"dir", "digest", "desc", "tries"}

    @classmethod
    def factory(cls, config, client=None):
        return lambda case: cls(config, client)

    # ---------------- 描述 ----------------
    async def _describe(self, state, digest: PageDigest, last_step: dict | None, vdir: Path) -> PageDescription:
        need, why = self.cache.need(state, last_step)
        if not need:
            d = self.cache.last
            desc = PageDescription(d.regions, d.overlay, True, d.raw, why)
            _dump(vdir / "describe.json", {**desc.to_dict(), "calls": []})
            return desc
        system = self.pb.render("system")
        user = self.pb.render("describe", kinds="、".join(REGION_KINDS))
        calls, last_err = [], ""
        for _ in range(DESCRIBE_TRIES):
            r = await self.client.chat(system, user, [digest.clean_png], DESCRIBE_SCHEMA)
            calls.append({"elapsed_ms": r.elapsed_ms, "prompt_tokens": r.prompt_tokens,
                          "output_tokens": r.output_tokens, "raw": r.text})
            try:
                obj = _parse_json(r.text)
                regions = [x for x in obj.get("regions", [])
                           if isinstance(x, dict) and x.get("kind") in REGION_KINDS]
                for x in regions:
                    if x.get("position") not in POSITIONS:
                        x["position"] = "中部"
                desc = PageDescription(regions, bool(obj.get("overlay")), False, r.text, why)
                self.cache.store(state, desc)
                _dump(vdir / "describe.json", {**desc.to_dict(), "calls": calls, "prompt": user})
                return desc
            except (ValueError, AttributeError, TypeError) as e:
                last_err = f"{type(e).__name__}: {e}"
        desc = PageDescription(reason=f"描述失败（{DESCRIBE_TRIES} 次输出都不合格式）：{last_err}")
        _dump(vdir / "describe.json", {**desc.to_dict(), "calls": calls, "prompt": user})
        return desc

    # ---------------- 决策 ----------------
    def _recent_steps(self, task) -> str:
        rows = []
        for s in (task.recent_steps or [])[-5:]:
            tool = s.get("tool")
            args = dict(s.get("arguments") or {})
            if s.get("target_label"):
                args.pop("element", None)
                what = f"{tool} {s['target_label']}" + (f" {json.dumps(args, ensure_ascii=False)}" if args else "")
            else:
                what = f"{tool} {json.dumps(args, ensure_ascii=False)}"
            res = "成功" if s.get("ok") else "失败"
            if s.get("ok"):
                res += "，页面有变化" if s.get("effective") else "，页面没有变化"
            if s.get("error"):
                res += f"（{str(s['error'])[:120]}）"
            if s.get("data") and tool in ("read_text", "read_links", "extract"):
                res += f"；读到：{json.dumps(s['data'], ensure_ascii=False)[:300]}"
            rows.append(f"- {what} → {res}")
        return "\n".join(rows) if rows else "（这是本任务的第一步）"

    def _render_decide(self, inp, digest: PageDigest, desc: PageDescription) -> str:
        t, st, brief = inp.task, inp.page, inp.brief or {}
        children = "；".join(f"{c.get('goal')}：{c.get('summary') or c.get('status')}"
                            for c in (t.child_summaries or [])) or "无"
        last_error = ""
        if inp.last_error:
            last_error = f"【上一次的问题】你上一次的回答不合法：{_explain_error(inp.last_error)}。请改正后重新回答。\n"
        overlay = "【提示】页面有弹窗遮挡：请先关闭弹窗或遮罩，再继续任务。\n" if desc.overlay else ""
        text = self._render_decide_base(t, st, brief, children, last_error, overlay, digest, desc)
        caps = getattr(inp, "caps", None)
        if caps is not None and (getattr(caps, "right_click", False) or getattr(caps, "grid", False)) \
                and self._desktop_tools_relevant(inp):
            # 桌面驱动专用工具：插在工具清单末尾；CDP / Playwright 下提示词与之前逐字相同
            extra = self.pb.text("tools_desktop").rstrip("\n")
            marker = "\n\n做法提示："
            text = text.replace(marker, "\n" + extra + marker, 1) if marker in text else text + "\n" + extra + "\n"
        return text

    @staticmethod
    def _desktop_tools_relevant(inp) -> bool:
        """桌面工具说明只在用得上时才放进提示词：实测（2026-11-15，v02 第 5 步重放各 6 次）一直带着这几行，
        4b 在该抽取的页面上改选 read_text（6/6），去掉后选 extract（6/6）。"""
        els = getattr(getattr(inp, "page", None), "elements", None) or []
        if any(getattr(e, "source", "") == "grid" or getattr(e, "role", "") == "menuitem" for e in els):
            return True
        recent = list(getattr(getattr(inp, "task", None), "recent_steps", None) or [])
        if recent and recent[-1].get("tool") in ("right_click", "show_grid"):
            return True
        last2 = recent[-2:]
        return len(last2) == 2 and all(s.get("tool") in ("click", "type", "scroll") and not s.get("effective")
                                       for s in last2)

    def _render_decide_base(self, t, st, brief, children, last_error, overlay, digest, desc) -> str:
        return self.pb.render(
            "decide", goal=t.goal, done_when=t.done_when or "（未写明）",
            hints="、".join(map(str, brief.get("hints") or [])) or "无",
            extract_fields="、".join(map(str, brief.get("extract_fields") or [])) or "无",
            depth=t.depth, steps_used=t.steps_used, max_steps=t.max_steps, child_summaries=children,
            recent_steps=self._recent_steps(t), last_error=last_error, overlay_hint=overlay,
            url=st.url, title=st.title, description=desc.text(), elements=digest.element_lines(),
            body=digest.body_summary or "（无）")

    @staticmethod
    def _map_numbers(obj: dict, digest: PageDigest) -> dict:
        args = dict(obj.get("arguments") or {})
        if "element" in args:
            no = _as_no(args["element"])
            eid = digest.to_id(no) if no is not None else None
            args["element"] = eid if eid else f"#{no if no is not None else args['element']}"
        if obj.get("tool") == "extract" and isinstance(args.get("items"), list):
            items = []
            for it in args["items"]:
                if not isinstance(it, dict):
                    items.append(it)
                    continue
                it = dict(it)
                if "value" in it and not isinstance(it["value"], str):
                    it["value"] = str(it["value"])
                el = it.get("element")
                no = _as_no(el)
                if el in (None, "", "正文", "body") or no == 0:
                    it.pop("element", None)
                else:
                    eid = digest.to_id(no) if no is not None else None
                    it["element"] = eid if eid else f"#{no if no is not None else el}"
                items.append(it)
            args["items"] = items
        return {"decision": "call_tool", "reason": str(obj.get("reason", "")), "tool": obj.get("tool"),
                "arguments": args}

    async def decide(self, inp) -> str:
        sdir = Path(inp.step_dir) if inp.step_dir else None
        vdir = (sdir / "vlm") if sdir else Path(".") / "vlm_unsaved"
        if self._step.get("dir") != sdir or sdir is None:
            png = _blank_png()
            if sdir is not None and inp.page.screenshot and (sdir / inp.page.screenshot).exists():
                png = (sdir / inp.page.screenshot).read_bytes()
            goal = f"{inp.task.goal} {inp.task.done_when or ''}"
            digest = PageDigest.build(inp.page, png, inp.brief, goal, self.s)
            vdir.mkdir(parents=True, exist_ok=True)
            (vdir / "clean.png").write_bytes(digest.clean_png)
            (vdir / "annotated.png").write_bytes(digest.annotated_png)
            _dump(vdir / "digest.json", digest.to_dict())
            last_step = (inp.task.recent_steps or [None])[-1]
            desc = await self._describe(inp.page, digest, last_step, vdir)
            self._step = {"dir": sdir, "digest": digest, "desc": desc, "tries": 0}
        digest, desc = self._step["digest"], self._step["desc"]
        self._step["tries"] += 1
        suffix = "" if self._step["tries"] == 1 else f"_try{self._step['tries']}"
        system = self.pb.render("system")
        user = self._render_decide(inp, digest, desc)
        schema = decide_schema(getattr(inp, "caps", None))
        _dump(vdir / f"request{suffix}.json", {
            "system": system, "user": user, "images": ["annotated.png"], "schema": schema,
            "model": self.client.model, "options": {k: self.s[k] for k in ("temperature", "seed", "num_ctx")}})
        r = await self.client.chat(system, user, [digest.annotated_png], schema)
        out = r.text
        mapped = None
        try:
            obj = _parse_json(r.text)
            if isinstance(obj, dict):
                mapped = self._map_numbers(obj, digest)
                out = json.dumps(mapped, ensure_ascii=False)
        except ValueError:
            pass
        _dump(vdir / f"response{suffix}.json", {
            "raw": r.text, "mapped": mapped, "elapsed_ms": r.elapsed_ms, "prompt_tokens": r.prompt_tokens,
            "output_tokens": r.output_tokens, "describe_reused": desc.reused})
        return out
