"""运行结果与报告页：末端叶子（带依据）、排序输出、保留的标签页、修改记录、每次消耗的积木产出。"""
from __future__ import annotations

import html as H
import json
import re
import time
from pathlib import Path

from ..expand.rank import parse_price


def _num(s) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(s or "").replace(",", ""))
    return float(m.group()) if m else None


def field_value(name: str, text):
    """排序用：价位换成档位，其它取第一个数字；识别不了为 None。"""
    if text is None or text == "":
        return None
    if "价" in name or "price" in name.lower():
        return parse_price(str(text))
    return _num(text)


def build_result(tree, book, evidence: dict | None = None, state: str = "done") -> dict:
    """evidence：(键, 词) → 依据 {step, text, url, element, fields: {名: {text, element, url}}}。"""
    evidence = evidence or {}
    leaves = []
    for n in tree.leaves():
        key = tree.key_of(n.id)
        ev = evidence.get((key, n.word)) or {}
        last = next((r for r in reversed(n.reasons) if r.get("actor") in ("rule", "user", "model")), None)
        leaves.append({"id": n.id, "word": n.word, "key": key, "url": n.url or ev.get("url"), "shot": n.shot,
                       "fields": {k: v.get("text") for k, v in (ev.get("fields") or {}).items()},
                       "evidence": ev or None, "origin": n.origin,
                       "reason": (last or {}).get("reason") or (last or {}).get("op"),
                       "step": (last or {}).get("step")})
    out = {"name": book.name, "title": book.title, "state": state, "counts": tree.counts(), "leaves": leaves,
           "tabs": [{"node": n.id, "word": n.word, "tab": str(n.tab), "url": n.url}
                    for n in tree.nodes.values() if n.tab is not None and n.state != "deleted"],
           "edits": [e.to_dict() for e in tree.events if e.actor == "user"],
           "mapping": tree.mapping, "values": book.values}
    o = book.output or {}
    if o.get("type") == "ranked":
        out["ranked"] = ranked(leaves, o)
    return out


def ranked(leaves: list[dict], o: dict) -> dict:
    sort = [str(s) for s in o.get("sort") or []]
    specs = [(f, order) for f, _, order in (s.partition(":") for s in sort)]
    n = int(o.get("n", 5))
    rows = []
    for lf in leaves:
        vals = {f: field_value(f, lf["fields"].get(f)) for f, _ in specs}
        rows.append({**lf, "values": vals, "known": all(v is not None for v in vals.values())})

    def key(r):
        out = []
        for f, order in specs:
            v = r["values"][f]
            out.append((v is None, (-v if order == "desc" else v) if v is not None else 0))
        return tuple(out)
    unknown_mode = o.get("unknown", "last")
    groups: dict[str, list] = {}
    for r in rows:
        if unknown_mode == "exclude" and not r["known"]:
            continue
        groups.setdefault(str(r["key"] or "") if o.get("group_by") == "key" else "", []).append(r)
    top = {}
    for g, rs in groups.items():
        seen, uniq = set(), []
        for r in sorted(rs, key=key):
            if r["word"] in seen:
                continue
            seen.add(r["word"])
            uniq.append(r)
        top[g] = uniq[:n]
    return {"top": top, "unknown": [r for r in rows if not r["known"]], "mode": unknown_mode, "sort": sort, "n": n}


# ---------------- 报告页 ----------------
NAMES = {"pending": "待查", "key": "键", "tentative": "暂定", "terminal": "末端叶子", "deleted": "已删除"}


def _steps_html(run_dir: Path) -> str:
    e = H.escape
    parts = []
    for f in sorted((run_dir / "nodes").glob("*/blocks.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = []
        for b in d.get("blocks", []):
            out = b.get("out") or []
            dropped = b.get("dropped") or []
            extra = []
            if b.get("error"):
                extra.append("出错：" + str(b["error"]))
            if b.get("note"):
                extra.append(str(b["note"]))
            if b.get("rejected"):
                extra.append(f"被拒编号 {len(b['rejected'])} 个")
            if dropped:
                extra.append("滤掉：" + "、".join(f"{x.get('text')}（{x.get('reason')}）" for x in dropped[:12]))
            if b.get("op"):
                extra.append("改树：" + json.dumps(b["op"], ensure_ascii=False))
            rows.append(f"<tr><td>{e(b.get('step', ''))}</td><td>{e(b.get('label') or b.get('block', ''))}</td>"
                        f"<td>{e('、'.join(str(x.get('text')) for x in out[:15]))}</td><td>{e('；'.join(extra))}</td></tr>")
        shots = " ".join(f'<a href="nodes/{e(f.parent.name)}/{e(s)}">{e(s)}</a>'
                         for b in d.get("blocks", []) for s in b.get("shots", []))
        parts.append(f"<h3>{e(f.parent.name)}（消耗 {d.get('cost', 0)}）</h3><p>{shots}</p>"
                     "<table><tr><th>步骤</th><th>积木</th><th>产出</th><th>说明</th></tr>" + "".join(rows) + "</table>")
    return "".join(parts) or "<p>（没有记录）</p>"


def report_html(tree, book, result: dict, run_dir) -> str:
    e = H.escape
    t, res = tree, result or {}

    def node_li(nid):
        n = t.nodes[nid]
        kids = "".join(node_li(c) for c in n.children)
        last = next((r for r in reversed(n.reasons) if r.get("reason") or r.get("step")), None)
        why = ""
        if last:
            why = " " + e(str(last.get("reason") or "")) + (f" ← {e(str(last['step']))}" if last.get("step") else "")
        return (f"<li class='{n.state}'>{e(n.word)} <small>[{NAMES.get(n.state, n.state)}·{e(n.origin)}·查 {n.searches}]"
                f"{why}</small>" + (f"<ul>{kids}</ul>" if kids else "") + "</li>")
    roots = "".join(node_li(n.id) for n in t.nodes.values() if n.parent is None)
    rows = "".join(f"<tr><td>{i}</td><td>{e(s['why'])}</td><td>{s['leaves']}</td><td>{s['nodes']}</td>"
                   f"<td>{s['used']} / {s['budget']}</td></tr>" for i, s in enumerate(t.series, 1))
    ev = "".join(f"<tr><td>{e(time.strftime('%H:%M:%S', time.localtime(x.t)))}</td><td>{e(x.actor)}</td>"
                 f"<td>{e(x.op)}</td><td>{e(t.nodes[x.node].word if x.node in t.nodes else '')}</td>"
                 f"<td><code>{e(json.dumps(x.detail, ensure_ascii=False, default=str))[:160]}</code></td></tr>"
                 for x in t.events if x.op not in ("create",))
    res_html = ""
    rk = res.get("ranked")
    if rk:
        fields = [s.partition(":")[0] for s in rk.get("sort", [])]
        for g, items in rk["top"].items():
            res_html += (f"<h3>{e(g or '结果')}</h3><table><tr><th>#</th><th>名称</th>"
                         + "".join(f"<th>{e(f)}</th>" for f in fields) + "<th>网址</th></tr>" + "".join(
                f"<tr><td>{i}</td><td>{e(x['word'])}</td>"
                + "".join(f"<td>{e(str(x['fields'].get(f) or '未知'))}</td>" for f in fields)
                + f"<td>{e(str(x.get('url') or ''))}</td></tr>" for i, x in enumerate(items, 1)) + "</table>")
        if rk.get("unknown"):
            res_html += "<p>字段未知（" + ("排在已知之后" if rk.get("mode") == "last" else "未参与排序") + "）：" + \
                "、".join(e(x["word"]) for x in rk["unknown"]) + "</p>"
    else:
        res_html = "<ul>" + "".join(
            f"<li>{e(lf['word'])} <small>{e(str(lf.get('key') or ''))} · {e(str(lf.get('reason') or ''))}"
            f" {e(str(lf.get('url') or ''))}</small></li>" for lf in res.get("leaves", [])) + "</ul>"
    title = book.title or book.name
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>任务书运行 {e(title)}</title>
<style>body{{font:14px sans-serif;margin:24px}} table{{border-collapse:collapse;margin:8px 0}} td,th{{border:1px solid #ccc;padding:3px 8px;vertical-align:top}}
li.terminal{{color:#1a7f4b}} li.key{{color:#2f6fd6}} li.deleted{{color:#999;text-decoration:line-through}} small{{color:#666}}</style></head><body>
<h1>任务书运行：{e(title)}</h1>
<p>状态 {e(str(res.get('state')))}；{e(t.summary_text())}</p>
<h2>结果</h2>{res_html}
<h2>最终的树</h2><ul>{roots}</ul>
<h2>每次消耗</h2>{_steps_html(Path(run_dir))}
<h2>变化时间线</h2><table><tr><th>#</th><th>原因</th><th>末端叶子</th><th>节点</th><th>已用</th></tr>{rows}</table>
<h2>事件（含修改）</h2><table><tr><th>时间</th><th>谁</th><th>操作</th><th>节点</th><th>详情</th></tr>{ev}</table>
</body></html>"""
