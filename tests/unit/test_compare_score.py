"""人眼比对：出题、单页评分、汇总计数与比例。"""
from hands.vlm.compare import load_set, make_questions, score_page, summarize


def _els():
    return [{"no": 1, "role": "searchbox", "label": "搜索商品"}, {"no": 2, "role": "button", "label": "搜索"},
            {"no": 3, "role": "link", "label": "Spicy Ramen"}, {"no": 4, "role": "link", "label": "1"},
            {"no": 5, "role": "link", "label": "Beef Noodle Soup"}]


def test_make_questions_deterministic():
    q1, q2 = make_questions(_els(), 7), make_questions(_els(), 7)
    assert q1 == q2 and [q["type"] for q in q1] == ["select", "select", "areas", "text", "text", "text"]
    assert all(2 <= len(q["dom"]) for q in q1 if q["type"] == "text")       # "1" 太短，不会被选中


def test_score_and_summary():
    qs = make_questions(_els(), 7)
    texts = [q for q in qs if q["type"] == "text"]
    human = {"search": 1, "first": 3, "areas": ["导航栏", "搜索区", "商品卡片"],
             texts[0]["id"]: texts[0]["dom"], texts[1]["id"]: texts[1]["dom"], texts[2]["id"]: texts[2]["dom"]}
    model = {"search": 1, "first": 5, "areas": ["导航栏", "商品卡片", "页脚"],
             texts[0]["id"]: texts[0]["dom"].upper(), texts[1]["id"]: "看不清", texts[2]["id"]: texts[2]["dom"]}
    rows = score_page(qs, human, model)
    s = summarize({"p1": rows})
    assert s["select"] == {"n": 2, "agree_rate": 0.5}
    assert s["areas"]["missed"] == 1 and s["areas"]["false"] == 1
    assert s["areas"]["per_kind"] == {"搜索区": {"missed": 1, "false": 0}, "页脚": {"missed": 0, "false": 1}}
    assert s["text"]["human_model"] == round(2 / 3, 3) and s["text"]["human_dom"] == 1.0
    assert s["text"]["model_dom"] == round(2 / 3, 3)
    qids = {m["qid"] for m in s["mismatches"]}
    assert "first" in qids and "areas" in qids and texts[1]["id"] in qids


def test_unanswered_skipped():
    qs = make_questions(_els(), 1)
    s = summarize({"p": score_page(qs, {}, {"search": 1}), "q": score_page(qs, {"search": 1}, {"search": 1})})
    assert s["select"]["n"] == 1 and s["mismatches"] == []
    assert s["pages"] == 2 and s["answered"] == 1          # 只答了 q 页


def test_default_set_loads():
    d = load_set("compare/sets/default.yaml")
    assert d["name"] == "default" and len(d["pages"]) == 10
    assert sum("bing.com" in p["url"] for p in d["pages"]) == 5
