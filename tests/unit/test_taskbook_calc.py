import pytest

from hands.taskbook.calc import compare, filter_items, norm, rank, ratio
from hands.taskbook.engine import Item


def items(*texts, ctx=None):
    return [Item(t, f"e{i}", "u", context=(ctx[i] if ctx else "")) for i, t in enumerate(texts)]


def test_norm():
    assert norm("  Ｏｐｔｉｍｕｓ  Prime ") == "optimusprime"
    assert norm("AbC", ignore_case=False) == "AbC"


# ---------------- 数比例 ----------------
def test_ratio_threshold_and_top():
    res = items(*[f"标题{i}" for i in range(12)],
                ctx=["Megatron 出场"] * 8 + ["无关"] * 2 + ["megatron"] * 2)
    out, log = ratio(res, 10, ["megatron", "Optimus", "optimus"], 0.8, current="optimus")
    assert [o.text for o in out] == ["megatron"]
    assert out[0].origin == {"ratio": 0.8, "hits": [1, 2, 3, 4, 5, 6, 7, 8], "of": 10}
    out, _ = ratio(res, 10, ["megatron"], 0.81)
    assert out == []
    seven = items(*[f"t{i}" for i in range(10)], ctx=["megatron"] * 7 + ["无关"] * 3)
    out, log = ratio(seven, 10, ["megatron"], 0.8)                 # 7/10
    assert out == [] and log[0]["ratio"] == 0.7
    # 当前词被去掉
    out, log = ratio(items("optimus a", "optimus b"), 10, ["optimus"], 0.5, current="Optimus", drop_current=True)
    assert out == [] and log == []
    out, _ = ratio(items("optimus a", "optimus b"), 10, ["optimus"], 0.5, current="Optimus", drop_current=False)
    assert [o.text for o in out] == ["optimus"]
    assert ratio([], 10, ["x"], 0.5)[0] == []


def test_ratio_fullwidth_case():
    out, _ = ratio(items("ＭＥＧＡＴＲＯＮ is back", "about Megatron"), 10, [Item("megatron")], 1.0)
    assert [o.text for o in out] == ["megatron"]


# ---------------- 取名次 ----------------
def test_rank():
    xs = items("a", "b", "c")
    assert [x.text for x in rank(xs, k=2)[0]] == ["b"]
    out, log = rank(xs, k=5)
    assert out == [] and "没有第 5 名" in log[0]["why"]
    assert [x.text for x in rank(xs, top_k=2)[0]] == ["a", "b"]
    out, log = rank(xs, top_k=5)
    assert len(out) == 3 and "不足" in log[0]["why"]


# ---------------- 过滤 ----------------
def test_filter_alias_then_current():
    xs = items("擎天柱", "Optimus Prime", "Megatron", "megatron", "Bumblebee", "SALE bumblebee toy")
    out, dropped = filter_items(xs, alias={"擎天柱": "optimus", "Optimus Prime": "optimus"}, stop=["toy"],
                                current="optimus", drop_current=True)
    assert [o.text for o in out] == ["Megatron", "Bumblebee"]
    reasons = {d["text"]: d["reason"] for d in dropped}
    assert reasons == {"擎天柱": "别名归一后是当前词", "Optimus Prime": "别名归一后是当前词",
                       "megatron": "重复", "SALE bumblebee toy": "停用词"}


def test_filter_known():
    xs = items("Megatron (Transformers)", "starscream", "Unicron")
    known = ["megatron", "starscream", "optimus"]
    out, dropped = filter_items(xs, known=known, only_known=True, contains=True)
    assert [o.text for o in out] == ["megatron", "starscream"]
    assert out[0].origin["matched_from"] == "Megatron (Transformers)"
    assert dropped == [{"text": "Unicron", "reason": "不在已知词"}]
    out, dropped = filter_items(xs, known=known, drop_known=True)
    assert [o.text for o in out] == ["Megatron (Transformers)", "Unicron"]
    assert dropped == [{"text": "starscream", "reason": "已有词", "word": "starscream"}]


def test_filter_case_and_contains():
    xs = items("Megatron", "megatron")
    out, _ = filter_items(xs, ignore_case=False)
    assert len(out) == 2
    out, _ = filter_items(xs, ignore_case=True)
    assert len(out) == 1
    out, d = filter_items(items("the megatron toy"), current="megatron", drop_current=True, contains=False)
    assert len(out) == 1
    out, d = filter_items(items("the megatron toy"), current="megatron", drop_current=True, contains=True)
    assert out == [] and d[0]["reason"] == "当前词"


def test_filter_deterministic():
    xs = items("a", "B", "b", "c")
    assert [o.text for o in filter_items(xs)[0]] == [o.text for o in filter_items(xs)[0]] == ["a", "B", "c"]


# ---------------- 比较字段 ----------------
def shop(name, **fields):
    return Item(name, fields={k: Item(v) for k, v in fields.items()})


def test_compare_price():
    xs = [shop("A", 价位="$$"), shop("B", 价位="$$$"), shop("C", 价位="人均 ¥45"), shop("D", 价位="¥30-60"),
          shop("E", 评分="4.5"), shop("F", 价位="电话订座")]
    ok, unknown, dropped = compare(xs, "价位", "<=", "$$")
    assert [x.text for x in ok] == ["A", "C", "D"]
    assert [x.text for x in unknown] == ["E", "F"]
    assert {d["text"]: d["reason"] for d in dropped} == {"B": "比较不满足", "E": "未知", "F": "未知"}


def test_compare_number_and_contains():
    xs = [shop("A", 评分="4.6 (120)"), shop("B", 评分="评分 4.2"), shop("C", 地址="朝阳区 三里屯")]
    ok, unknown, _ = compare(xs, "评分", ">=", "4.5")
    assert [x.text for x in ok] == ["A"] and [x.text for x in unknown] == ["C"]
    ok, _, _ = compare(xs, "评分", "=", "4.2")
    assert [x.text for x in ok] == ["B"]
    ok, _, _ = compare(xs, "地址", "contains", "三里屯")
    assert [x.text for x in ok] == ["C"]
    with pytest.raises(ValueError):
        compare(xs, "评分", ">=", "很高")
