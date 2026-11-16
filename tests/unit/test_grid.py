from hands.desktop.grid import make_grid
from hands.geometry import Box


def test_full_grid():
    g = make_grid(1200, 800, 12, 8)
    assert len(g) == 96
    assert [e.name for e in g[:3]] == ["格 1", "格 2", "格 3"]
    assert g[0].id == "e1" and g[-1].name == "格 96"
    assert all(e.source == "grid" and e.role == "grid_cell" for e in g)
    assert (g[13].box.x, g[13].box.y) == (100.0, 100.0)       # 格 14：第 2 行第 2 列


def test_skip_occupied_and_ids_continue():
    big = Box(0, 0, 600, 400)             # 占左上 6×4 = 24 格
    g = make_grid(1200, 800, 12, 8, [big], start_id=10)
    assert len(g) == 96 - 24
    assert g[0].id == "e10" and g[0].name == "格 7"
    assert all(int(e.name.split()[1]) != 13 for e in g)
