"""人眼比对（spec ver1 F9）：同一张存下的截图，人和模型回答同一份提问，量出两者的视觉误差。

比对模式下模型只看图、不给 DOM 清单：选号题和文字题看标注图，区块题看干净截图。
DOM 里的文字只作为文字题的第三方参照。

目录：<DATA_ROOT>/compare/<比对集>/
  pages/<页面 id>/clean.png、annotated.png、elements.json、meta.json、questions.json、human.json、model/<模型>.json
  results/<时间>-<模型>/report.json、report.md
"""
from __future__ import annotations

import difflib
import json
import random
import re
import time
from pathlib import Path

import yaml

from .describe_cache import REGION_KINDS
from .digest import PageDigest
from .extract import normalize

SELECT_QS = [
    ("search", "哪个编号是搜索框（输入搜索词的地方）？没有就选“无”。"),
    ("first", "页面主要内容区里第一个条目（第一条搜索结果、第一个商品等）的标题是哪个编号？没有就选“无”。"),
]
SCHEMAS = {
    "select": {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"]},
    "text": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
    "areas": {"type": "object", "properties": {"answer": {"type": "array", "items": {"type": "string",
                                                                                      "enum": REGION_KINDS}}},
              "required": ["answer"]},
}


def _dump(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def safe_name(s: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", s)


# ---------------------------------------------------------------- 比对集与出题
def load_set(path) -> dict:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    pages = [{"id": str(p["id"]), "url": str(p["url"])} for p in d.get("pages") or []]
    return {"name": d.get("name") or Path(path).stem, "seed": int(d.get("seed", 1)), "pages": pages}


def make_questions(elements: list[dict], seed: int) -> list[dict]:
    qs = [{"id": qid, "type": "select", "text": text} for qid, text in SELECT_QS]
    qs.append({"id": "areas", "type": "areas", "text": "这张截图里出现了哪些区块类型？（可多选）", "kinds": REGION_KINDS})
    pool = [e for e in elements if 2 <= len((e.get("label") or "").strip()) <= 40]
    rnd = random.Random(seed)
    for i, e in enumerate(rnd.sample(pool, min(3, len(pool))), 1):
        qs.append({"id": f"text{i}", "type": "text", "no": e["no"], "text": f"编号 {e['no']} 的框里写的是什么？",
                   "dom": e["label"]})
    return qs


# ---------------------------------------------------------------- 采集
async def capture_page(page, url: str, page_dir: Path, settings: dict, seed: int) -> dict:
    from ..pagestate import build_page_state
    await page.navigate(url)
    state = await build_page_state(page, page_dir, shot_name="raw.png")
    png = (page_dir / "raw.png").read_bytes()
    digest = PageDigest.build(state, png, {}, "", {**settings, "offscreen_top_k": 0})
    (page_dir / "clean.png").write_bytes(digest.clean_png)
    (page_dir / "annotated.png").write_bytes(digest.annotated_png)
    els = []
    for n in digest.numbered:
        e = state.element(n.element_id)
        b = e.box
        els.append({"no": n.no, "role": e.role, "label": re.sub(r"\s+", " ", e.name or e.text or "").strip(),
                    "box": {"x": round(b.x * digest.scale, 1), "y": round(b.y * digest.scale, 1),
                            "w": round(b.width * digest.scale, 1), "h": round(b.height * digest.scale, 1)}})
    _dump(page_dir / "elements.json", els)
    from PIL import Image
    import io
    w, h = Image.open(io.BytesIO(digest.annotated_png)).size
    meta = {"url": url, "final_url": state.url, "title": state.title, "captured_at": time.time(),
            "scale": digest.scale, "size": [w, h]}
    _dump(page_dir / "meta.json", meta)
    _dump(page_dir / "questions.json", make_questions(els, seed))
    return meta


async def capture_set(page, set_def: dict, set_dir: Path, settings: dict, base: str, min_interval: dict,
                      log=print) -> list[str]:
    """逐页打开并存下截图、编号和题目。Bing 等外部网站按 min_interval（秒）留间隔。"""
    import asyncio
    from urllib.parse import urlparse
    done, last = [], {}
    for i, p in enumerate(set_def["pages"]):
        url = p["url"].replace("${BASE}", base)
        host = (urlparse(url).hostname or "").lower()
        for dom, gap in (min_interval or {}).items():
            if host == dom or host.endswith("." + dom):
                if dom in last:
                    wait = gap - (time.monotonic() - last[dom])
                    if wait > 0:
                        log(f"[比对采集] {dom}：距上次访问不足 {gap} 秒，等待 {wait:.0f} 秒")
                        await asyncio.sleep(wait)          # 对外部网站的礼貌间隔
                last[dom] = time.monotonic()
        log(f"[比对采集] {p['id']} ← {url}")
        await capture_page(page, url, set_dir / "pages" / p["id"], settings, set_def["seed"] + i)
        done.append(p["id"])
    return done


# ---------------------------------------------------------------- 模型作答
async def ask_model(page_dir: Path, client, prompts) -> dict:
    qs = _load(page_dir / "questions.json", [])
    annotated = (page_dir / "annotated.png").read_bytes()
    clean = (page_dir / "clean.png").read_bytes()
    system = "你是一个看网页截图回答问题的助手。只根据图片回答，严格输出要求的 JSON。"
    answers, calls = {}, []
    for q in qs:
        if q["type"] == "select":
            user, img = prompts.render("compare_select", question=q["text"]), annotated
        elif q["type"] == "text":
            user, img = prompts.render("compare_text", no=q["no"]), annotated
        else:
            user, img = prompts.render("compare_areas", kinds="、".join(REGION_KINDS)), clean
        r = await client.chat(system, user, [img], SCHEMAS[q["type"]])
        ans = None
        try:
            ans = json.loads(r.text).get("answer")
        except (ValueError, AttributeError):
            pass
        answers[q["id"]] = ans
        calls.append({"qid": q["id"], "raw": r.text, "elapsed_ms": r.elapsed_ms})
    out = {"model": client.model, "answers": answers, "calls": calls, "answered_at": time.time()}
    _dump(page_dir / "model" / f"{safe_name(client.model)}.json", out)
    return out


# ---------------------------------------------------------------- 评分
def _sel(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _sim(a, b) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def score_page(questions: list[dict], human: dict, model: dict) -> list[dict]:
    rows = []
    for q in questions:
        h, m = human.get(q["id"]), model.get(q["id"])
        r = {"qid": q["id"], "type": q["type"], "question": q["text"], "human": h, "model": m}
        if h is None:
            r["skipped"] = True                      # 人还没答这题
        elif q["type"] == "select":
            r["agree"] = _sel(h) == _sel(m)
        elif q["type"] == "text":
            dom = q.get("dom", "")
            r.update(dom=dom, agree=normalize(h) == normalize(m), human_dom=normalize(h) == normalize(dom),
                     model_dom=normalize(m) == normalize(dom), sim=round(_sim(h, m), 3))
        else:
            hs, ms = set(h or []), set(m or [])
            r.update(missed=sorted(hs - ms), false=sorted(ms - hs), agree=hs == ms)
        rows.append(r)
    return rows


def summarize(pages: dict[str, list[dict]]) -> dict:
    sel = [r for rs in pages.values() for r in rs if r["type"] == "select" and not r.get("skipped")]
    txt = [r for rs in pages.values() for r in rs if r["type"] == "text" and not r.get("skipped")]
    ars = [r for rs in pages.values() for r in rs if r["type"] == "areas" and not r.get("skipped")]
    per_kind = {k: {"missed": 0, "false": 0} for k in REGION_KINDS}
    for r in ars:
        for k in r["missed"]:
            per_kind.setdefault(k, {"missed": 0, "false": 0})["missed"] += 1
        for k in r["false"]:
            per_kind.setdefault(k, {"missed": 0, "false": 0})["false"] += 1

    def rate(xs, key="agree"):
        return round(sum(1 for x in xs if x.get(key)) / len(xs), 3) if xs else None
    mism = [{"page": pid, **{k: r.get(k) for k in ("qid", "question", "human", "model", "dom", "missed", "false")}}
            for pid, rs in pages.items() for r in rs if not r.get("skipped") and not r.get("agree")]
    return {
        "pages": len(pages),
        "answered": sum(1 for rs in pages.values() if any(not r.get("skipped") for r in rs)),   # 人至少答了一题的页数
        "select": {"n": len(sel), "agree_rate": rate(sel)},
        "text": {"n": len(txt), "human_model": rate(txt), "human_dom": rate(txt, "human_dom"),
                 "model_dom": rate(txt, "model_dom"),
                 "avg_similarity": round(sum(r["sim"] for r in txt) / len(txt), 3) if txt else None},
        "areas": {"n": len(ars), "missed": sum(v["missed"] for v in per_kind.values()),
                  "false": sum(v["false"] for v in per_kind.values()),
                  "per_kind": {k: v for k, v in per_kind.items() if v["missed"] or v["false"]}},
        "mismatches": mism,
    }


def report(set_dir: Path, model_name: str) -> Path:
    pages = {}
    for pdir in sorted((set_dir / "pages").glob("*")):
        qs = _load(pdir / "questions.json", [])
        human = (_load(pdir / "human.json", {}) or {}).get("answers", {})
        model = (_load(pdir / "model" / f"{safe_name(model_name)}.json", {}) or {}).get("answers", {})
        if qs and model:
            pages[pdir.name] = score_page(qs, human, model)
    s = summarize(pages)
    out = set_dir / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_name(model_name)}"
    k = 1
    while out.exists():
        k += 1
        out = out.with_name(out.name + f"-{k}")
    _dump(out / "report.json", {"model": model_name, "summary": s, "pages": pages})
    pct = lambda v: "-" if v is None else f"{v * 100:.0f}%"  # noqa: E731
    lines = [f"# 人眼比对报告：{set_dir.name} × {model_name}", "",
             f"人已作答 {s['answered']} / {s['pages']} 页（未答的题不计入）　生成时间 {time.strftime('%Y-%m-%d %H:%M')}", "",
             "| 项目 | 题数 | 结果 |", "|---|---|---|",
             f"| 选号一致率（人 vs 模型） | {s['select']['n']} | {pct(s['select']['agree_rate'])} |",
             f"| 文字读取一致率（人 vs 模型） | {s['text']['n']} | {pct(s['text']['human_model'])}，平均相似度 {s['text']['avg_similarity']} |",
             f"| 文字：人 vs DOM | {s['text']['n']} | {pct(s['text']['human_dom'])} |",
             f"| 文字：模型 vs DOM | {s['text']['n']} | {pct(s['text']['model_dom'])} |",
             f"| 区块识别：模型漏报 / 错报 | {s['areas']['n']} 页 | {s['areas']['missed']} / {s['areas']['false']} |", ""]
    if s["areas"]["per_kind"]:
        lines += ["| 区块类型 | 漏报 | 错报 |", "|---|---|---|"]
        lines += [f"| {k} | {v['missed']} | {v['false']} |" for k, v in s["areas"]["per_kind"].items()]
        lines.append("")
    if s["mismatches"]:
        lines += ["## 不一致的题", "", "| 页面 | 题 | 人 | 模型 | DOM |", "|---|---|---|---|---|"]
        cell = lambda v: " ".join(str(v if v is not None else "").split()).replace("|", "\\|")  # noqa: E731  换行会截断表格行
        for m in s["mismatches"]:
            lines.append(f"| {m['page']} | {cell(m['question'])} | {cell(m['human'])} | {cell(m['model'])} "
                         f"| {cell(m.get('dom'))} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
