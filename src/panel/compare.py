"""面板"人眼比对"页的后台：比对集列表、采集任务、作答保存、让模型作答任务、报告（spec ver1 F9）。

核心逻辑在 hands.vlm.compare；这里只做面板侧的胶水。采集和模型作答作为面板任务运行，和其他任务互斥。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from hands.config import DATA_ROOT, PROJECT_ROOT, vlm_settings
from hands.vlm import compare as core

_NAME = re.compile(r"^[\w\-]+$")
FILES = {"clean.png", "annotated.png"}


def sets_dir() -> Path:
    d = DATA_ROOT / "compare" / "sets"
    return d if d.exists() else PROJECT_ROOT / "compare" / "sets"


def set_dir(name: str) -> Path:
    if not _NAME.match(name or ""):
        raise ValueError(f"比对集名字不合法：{name!r}")
    return DATA_ROOT / "compare" / name


def page_dir(name: str, pid: str) -> Path:
    if not _NAME.match(pid or ""):
        raise ValueError(f"页面 id 不合法：{pid!r}")
    return set_dir(name) / "pages" / pid


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def list_sets() -> list[dict]:
    out = []
    for f in sorted(sets_dir().glob("*.yaml")):
        d = core.load_set(f)
        sd = set_dir(d["name"])
        pages = []
        for p in d["pages"]:
            pd = sd / "pages" / p["id"]
            human = (_load(pd / "human.json", {}) or {}).get("answers", {})
            qs = _load(pd / "questions.json", []) or []
            pages.append({"id": p["id"], "url": p["url"], "captured": (pd / "annotated.png").exists(),
                          "answered": sum(1 for q in qs if q["id"] in human), "questions": len(qs),
                          "models": sorted(x.stem for x in (pd / "model").glob("*.json"))})
        results = sorted((x.name for x in (sd / "results").glob("*") if x.is_dir()), reverse=True)
        out.append({"name": d["name"], "file": f.name, "pages": pages, "results": results})
    return out


def set_def(name: str) -> dict:
    for f in sets_dir().glob("*.yaml"):
        d = core.load_set(f)
        if d["name"] == name:
            return d
    raise FileNotFoundError(f"没有这个比对集：{name}")


def page_info(name: str, pid: str) -> dict:
    pd = page_dir(name, pid)
    if not (pd / "annotated.png").exists():
        raise FileNotFoundError("这一页还没有采集")
    return {"set": name, "page": pid, "meta": _load(pd / "meta.json", {}),
            "questions": _load(pd / "questions.json", []), "elements": _load(pd / "elements.json", []),
            "human": (_load(pd / "human.json", {}) or {}).get("answers", {}),
            "images": {k: f"/api/compare/img/{name}/{pid}/{k}" for k in ("clean.png", "annotated.png")}}


def save_answer(name: str, pid: str, answers: dict) -> dict:
    pd = page_dir(name, pid)
    qs = {q["id"]: q for q in _load(pd / "questions.json", []) or []}
    if not qs:
        raise FileNotFoundError("这一页还没有采集")
    clean = {}
    for qid, v in (answers or {}).items():
        q = qs.get(qid)
        if q is None:
            continue
        if q["type"] == "select":
            clean[qid] = int(v) if str(v).strip().lstrip("-").isdigit() else 0
        elif q["type"] == "text":
            clean[qid] = str(v)
        else:
            clean[qid] = [k for k in (v or []) if k in core.REGION_KINDS]
    old = (_load(pd / "human.json", {}) or {}).get("answers", {})
    data = {"answers": {**old, **clean}}
    (pd / "human.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def image_path(name: str, pid: str, file: str) -> Path:
    if file not in FILES:
        raise ValueError("只能读取截图文件")
    return page_dir(name, pid) / file


def latest_report(name: str) -> dict:
    sd = set_dir(name)
    dirs = sorted((x for x in (sd / "results").glob("*") if x.is_dir()), key=lambda x: x.name, reverse=True)
    if not dirs:
        raise FileNotFoundError("还没有报告：先作答，再让模型作答")
    d = dirs[0]
    return {"dir": d.name, "report": _load(d / "report.json", {}),
            "markdown": (d / "report.md").read_text(encoding="utf-8") if (d / "report.md").exists() else ""}


# ---------------------------------------------------------------- 面板任务
async def job_capture(jobs, job, params: dict):
    svc = jobs.svc
    d = set_def(params.get("set") or "default")
    svc.ensure_fixtures()
    total = len(d["pages"])
    job.progress = {"done": 0, "total": total}

    def log(msg):
        svc.log(msg)
        job.progress = {**job.progress, "line": msg}
        svc.push_soon()
    sd = set_dir(d["name"])
    base = svc.fixture_vars()["BASE"]
    for i, p in enumerate(d["pages"]):
        if jobs.stop_requested:
            break
        one = {**d, "pages": [p], "seed": d["seed"] + i}
        await core.capture_set(svc.browser.page, one, sd, vlm_settings(svc.config), base,
                               svc.config.min_interval_s, log)
        job.progress = {"done": i + 1, "total": total}
        svc.push_soon()
    job.result = {"passed": True, "reason": f"已采集 {job.progress['done']} / {total} 页", "set": d["name"]}


async def job_model(jobs, job, params: dict):
    from hands.vlm.client import OllamaClient
    from hands.vlm.prompts import PromptBook
    svc = jobs.svc
    name = params.get("set") or "default"
    s = vlm_settings(svc.config)
    if params.get("model"):
        s["model"] = str(params["model"])
    client = OllamaClient(s)
    await client.check()
    pages = [x for x in sorted((set_dir(name) / "pages").glob("*")) if (x / "annotated.png").exists()]
    if not pages:
        raise FileNotFoundError("这个比对集还没有采集")
    pb = PromptBook(s.get("prompts_dir"))
    job.progress = {"done": 0, "total": len(pages)}
    for i, pd in enumerate(pages):
        if jobs.stop_requested:
            break
        await core.ask_model(pd, client, pb)
        job.progress = {"done": i + 1, "total": len(pages), "line": f"模型已作答 {pd.name}"}
        svc.push_soon()
    out = core.report(set_dir(name), client.model)
    job.result = {"passed": True, "reason": f"模型 {client.model} 已作答 {job.progress['done']} 页",
                  "report": out.name, "set": name}
