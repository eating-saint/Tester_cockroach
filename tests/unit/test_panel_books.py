import json
import shutil
from pathlib import Path

import pytest

from hands.taskbook.book import BookConflict
from panel.books import BookService

ROOT = Path(__file__).resolve().parents[2]
LEGACY = Path(__file__).parent / "data" / "legacy"


@pytest.fixture
def svc(tmp_path):
    for p in (ROOT / "tasks").glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    return BookService(tmp_path)


def test_list_and_get(svc):
    rows = {r["name"]: r for r in svc.list()}
    assert {"dinner", "dinner_yelp", "fixture_dinner", "fixture_transformers", "transformers"} <= set(rows)
    assert all(r["status"] == "ok" for r in rows.values())
    assert [p["name"] for p in rows["fixture_dinner"]["params"]] == ["location"]          # BASE 不给用户看
    assert rows["transformers"]["uses_4b"] is True
    g = svc.get("transformers")
    assert g["status"] == "ok" and g["problems"] == [] and g["book"]["start"] == "optimus"
    with pytest.raises(ValueError):
        svc.get("../x")
    with pytest.raises(FileNotFoundError):
        svc.get("nothing")


def test_validate_problems(svc):
    r = svc.validate("format: taskbook/1\nname: x\ngroups:\n  - a: [b]\nstart: a\nflow:\n  - fly: {}\n")
    assert r["status"] == "draft" and r["problems"][0]["step"] == "flow[0]" and r["problems"][0]["block"] == "fly"
    assert svc.validate("format: nope\n")["problems"][0]["step"] == "文件"


def test_import_legacy_and_list_old_file(svc, tmp_path):
    r = svc.import_((LEGACY / "dinner.yaml").read_text(encoding="utf-8"))
    assert r["legacy"] and r["status"] == "needs_rewrite" and "format: taskbook/1" in r["text"]
    assert not (tmp_path / "dinner.yaml").read_text(encoding="utf-8").startswith("# 关联展开任务")   # 不落盘
    shutil.copy(LEGACY / "dinner.yaml", tmp_path / "old_dinner.yaml")
    rows = {x["name"]: x for x in svc.list()}
    assert rows["old_dinner"]["status"] == "needs_rewrite" and rows["old_dinner"]["legacy"]


def test_save_conflict_and_backup(svc, tmp_path):
    g = svc.get("fixture_dinner")
    text2 = g["text"].replace("title: 今晚吃什么（夹具）", "title: 改过的标题")
    r = svc.save("fixture_dinner", text2, g["rev"])
    assert r["status"] == "ok" and r["rev"] != g["rev"]
    assert len(list((tmp_path / ".bak").glob("fixture_dinner.*.yaml"))) == 1
    with pytest.raises(BookConflict, match="另一端"):
        svc.save("fixture_dinner", g["text"], g["rev"])
    r = svc.save("my_new", text2, None, save_as=True)
    assert (tmp_path / "my_new.yaml").exists()
    with pytest.raises(BookConflict, match="同名"):
        svc.save("my_new", text2, None, save_as=True)
    bad = "format: taskbook/1\nname: d\ngroups:\n  - a: [b]\nstart: a\nflow: []\n"
    assert svc.save("draft1", bad, None, save_as=True)["status"] == "draft"        # 草稿可以存


def test_preview_order(svc):
    r = svc.preview(svc.get("fixture_transformers")["text"])
    labels = [x["label"] for x in r["order"]]
    assert labels[:7] == ["optimus", "optimus/galvatron", "optimus/megatron", "megatron/starscream",
                          "megatron/shockwave", "megatron/soundwave", "optimus/starscream"]
    assert labels[7:] == ["movie", "movie/fallen", "movie/decepticon", "movie/autobot"]
    assert len(r["initial"]["nodes"]) == 0 and r["final"]["finished"]


def test_guide_and_steps(svc, tmp_path):
    g = svc.guide()
    assert "`pick_list`（抽列表）" in g and "fixture_dinner" in g
    run = tmp_path / "runs" / "20261116-x"
    nd = run / "book" / "nodes" / "0001_optimus"
    nd.mkdir(parents=True)
    (nd / "blocks.json").write_text(json.dumps({"word": "optimus", "blocks": [{"step": "flow[0]", "shots": ["s01.png"]}]}),
                                    encoding="utf-8")
    st = BookService.steps(run)
    assert st[0]["word"] == "optimus" and st[0]["blocks"][0]["shot_urls"] == [
        "/runs/20261116-x/book/nodes/0001_optimus/s01.png"]
