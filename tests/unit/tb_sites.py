"""假的仿搜索 / 仿点评网站（按 fixtures/f22_search、f23_dinner 的数据生成同样结构的页面），供运行器单测用。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

from tb_fakes import FakeDoc, FakeEl, FakeSite

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8765"


def _q(url):
    return unquote_plus((parse_qs(urlparse(url).query).get("q") or [""])[0]).strip()


def search_site() -> FakeSite:
    script = json.loads((ROOT / "fixtures" / "f22_search" / "script.json").read_text(encoding="utf-8"))

    def route(url):
        q = _q(url)
        hit = script.get(q.lower(), {"results": [], "related": []})
        res = hit["results"] or [{"title": f"{q} 无关结果 {i + 1}", "snippet": "与本词关系不大的普通页面。"}
                                 for i in range(10)]
        els = [FakeEl(r["title"], 120 + i * 60, f"{BASE}/f22_search/result.html?q={q}&i={i}",
                      context=f"{r['title']} {r['snippet']}") for i, r in enumerate(res)]
        side = "相关搜索 " + " ".join(hit.get("related") or [])
        els += [FakeEl(w, 60 + i * 24, f"{BASE}/f22_search/search.html?q={w}", context=side, x=760)
                for i, w in enumerate(hit.get("related") or [])]
        body = "\n".join([f"「{q}」的搜索结果"] + [f"{r['title']}\n{r['snippet']}" for r in res] + side.split(" "))
        return FakeDoc(f"{q} - 夹具搜索", els, body=body, height=900)
    return FakeSite(route)


def dinner_site() -> FakeSite:
    d = json.loads((ROOT / "fixtures" / "f23_dinner" / "data.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for rs in d["cuisines"].values() for r in rs}

    def route(url):
        if "biz.html" in url:
            r = by_id[parse_qs(urlparse(url).query)["id"][0]]
            price = "价位：" + (r["price"] or "未标注")
            body = "\n".join([r["name"], price, f"评分：{r['rating']}", "菜单", *r["menu"]])
            return FakeDoc(f"{r['name']} - 夹具点评", [], body=body, height=800)
        q = _q(url)
        name = next((c for c in d["cuisines"] if c in q), None)
        if not name:
            return FakeDoc(f"{q} - 夹具点评", [], body="没有找到相关餐厅", height=800)
        els = [FakeEl(r["name"], 120 + i * 70, f"{BASE}/f23_dinner/biz.html?id={r['id']}",
                      context=f"{r['name']} 价位：{r['price'] or '未标注'} 评分：{r['rating']}")
               for i, r in enumerate(d["cuisines"][name])]
        return FakeDoc(f"{q} - 夹具点评", els, body="\n".join(e.context for e in els), height=800)
    return FakeSite(route)


def search_picker(region, lines):
    """替身 4b：按区块选编号（看元素清单里的链接地址）。"""
    if "相关" in region:
        return [no for no, _, rest in lines if "search.html?q=" in rest]
    if "餐厅" in region:
        return [no for no, _, rest in lines if "biz.html" in rest]
    return [no for no, _, rest in lines if "result.html" in rest]


def dinner_fields(names, els, lines):
    out = {}
    for n in names:
        if n == "店名":
            out[n] = lines[0].split(" ")[0] if lines else None
        else:
            pref = {"价位": "价位：", "评分": "评分："}.get(n, n)
            out[n] = next((l.split(" ")[0] for l in lines if re.match(rf"^T\d+ {pref}", l)), None)
    return out


def dinner_answer() -> dict:
    d = json.loads((ROOT / "fixtures" / "f23_dinner" / "data.json").read_text(encoding="utf-8"))
    return {k: v for k, v in d["answer"].items() if k != "rule"}
