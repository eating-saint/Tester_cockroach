import json
from pathlib import Path

from hands.expand.rank import parse_price, parse_rating, rank

ROOT = Path(__file__).resolve().parents[2]


def test_parse_price():
    assert [parse_price(s) for s in ("$", "$$", "$$$", "$$$$", "¥", "￥￥")] == [1, 2, 3, 4, 1, 2]
    assert parse_price("人均 ¥45") == 1          # 6.25 美元
    assert parse_price("人均 ¥120") == 2         # 16.7 美元
    assert parse_price("$10–16") == 1 and parse_price("$12-18") == 1 and parse_price("$25") == 2
    assert parse_price("¥30-60") == 1 and parse_price("人均 ¥200") == 2
    assert parse_price("") is None and parse_price("未标注") is None
    assert parse_rating("评分：4.6") == 4.6 and parse_rating(None) is None


def test_parse_price_in_long_text():
    assert parse_price("Italian · $$ · Open now") == 2
    assert parse_price("Sushi Bars, Japanese $$$ Closed until 5PM") == 3
    assert parse_price("川菜 · 人均 ¥85 · 朝阳区") == 1           # 11.8 美元
    assert parse_price("人均 ¥85 · 川菜") == parse_price("人均 ¥85")
    assert parse_price("Price range $10–16 per person") == 1
    assert parse_price("4.5 (1,203 reviews)") is None
    assert parse_price("Open now · 4.7 stars") is None
    assert parse_price("均价 60-80 元，环境好") == parse_price("60-80 元")
    assert parse_price("US$") is None


def test_rank_matches_fixture_answer():
    d = json.loads((ROOT / "fixtures" / "f23_dinner" / "data.json").read_text(encoding="utf-8"))
    items = [{"name": r["name"], "price_text": r["price"], "rating_text": str(r["rating"]), "grounded": True,
              "group": c} for c, rs in d["cuisines"].items() for r in rs]
    items.append({"name": "编出来的店", "price_text": "$", "rating_text": "5.0", "grounded": False, "group": "日本寿司"})
    out = rank(items, ["price:asc", "rating:desc"], n=2, group_by="key")
    for c in d["cuisines"]:
        assert [r["name"] for r in out["top"][c]] == d["answer"][c]
    assert [r["name"] for r in out["ungrounded"]] == ["编出来的店"]


def test_unknown_price_last():
    out = rank([{"name": "A", "price_text": "", "rating_text": "5", "grounded": True},
                {"name": "B", "price_text": "$$$$", "rating_text": "1", "grounded": True}], n=5)
    assert [r["name"] for r in out["top"][""]] == ["B", "A"]
