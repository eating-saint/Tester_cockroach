import json
import os

import pytest

app = pytest.importorskip("panel.app")


def test_pid_alive_self_and_dead():
    assert app.pid_alive(os.getpid())
    assert not app.pid_alive(0)
    assert not app.pid_alive(2 ** 31 - 2)


def test_stale_lock_is_taken_over(tmp_path):
    p = tmp_path / "panel.lock"
    p.write_text(json.dumps({"pid": 2 ** 31 - 2, "port": 1, "token": "x"}), encoding="utf-8")
    assert app.try_focus_existing(app.read_lock(p)) is False          # 进程不在 → 接管
    p.write_text(json.dumps({"pid": os.getpid(), "port": 1, "token": "x"}), encoding="utf-8")
    assert app.try_focus_existing(app.read_lock(p)) is False          # 进程在但面板不应答 → 接管
    assert app.read_lock(tmp_path / "none") is None
