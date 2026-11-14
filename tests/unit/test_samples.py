import json

import pytest

pytest.importorskip("aiohttp")

from panel.samples import SampleStore  # noqa: E402
from test_trajlib import make_raw  # noqa: E402


def raw_ok(dx=300, dy=0, **kw):
    r = make_raw(dx, dy, **kw)
    return r


def test_reject_reasons(tmp_path):
    st = SampleStore(tmp_path / "s.json")
    miss = raw_ok()
    miss["target"] = {"x": 0, "y": 0, "width": 10, "height": 10}
    assert st.add(miss)["reason"] == "miss"
    left = raw_ok(); left["left_area"] = True
    assert st.add(left)["reason"] == "left_area"
    slow = raw_ok(dur=6000)
    assert st.add(slow)["reason"] == "too_slow"
    few = raw_ok(n=3)
    assert st.add(few)["reason"] == "too_few"
    assert st.library.samples == []


def test_add_dedupe_delete_grid(tmp_path):
    st = SampleStore(tmp_path / "s.json")
    a = st.add(raw_ok())
    assert a["ok"] and a["direction"] == 0
    assert st.add(raw_ok())["reason"] == "duplicate"
    st.add(raw_ok(0, 300, device="touchpad"))
    g = st.grid()
    assert g["mouse"][0]["local"] == 1 and g["touchpad"][2]["local"] == 1
    assert st.delete(a["id"]) and st.grid()["mouse"][0]["local"] == 0
    assert SampleStore(tmp_path / "s.json").library.samples[0].device == "touchpad"   # 已写盘


def test_export_import(tmp_path):
    st = SampleStore(tmp_path / "s.json")
    for k in range(3):
        st.add(raw_ok(300, 20 * k))
    data = st.export()
    assert json.loads(data)["version"] == 1
    other = SampleStore(tmp_path / "o.json")
    assert other.import_(data, "merge")["added"] == 3
    assert other.import_(data, "merge")["added"] == 0
    assert len(other.library.samples) == 3
    empty = SampleStore(tmp_path / "e.json")
    assert other.import_(empty.export(), "replace")["total"] == 0
    assert other.import_(b'{"version": 9, "samples": []}', "merge")["ok"] is False
