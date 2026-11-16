import pytest

from hands.desktop.coords import Frame


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5])
def test_round_trip(scale):
    f = Frame(68, 161, 1264, 771, scale)
    for cx, cy in [(0, 0), (10.4, 20.6), (500, 300), (f.css_size[0] - 1, f.css_size[1] - 1)]:
        sx, sy = f.to_screen(cx, cy)
        bx, by = f.to_css(sx, sy)
        assert abs(bx - cx) <= 0.5 and abs(by - cy) <= 0.5


def test_box_and_contains():
    f = Frame.from_rect((100, 200, 1100, 900), 1.25)
    b = f.box_to_css((150, 250, 275, 300))
    assert (b.x, b.y, b.width, b.height) == (40.0, 40.0, 100.0, 40.0)
    assert f.css_size == (800.0, 560.0)
    assert f.contains_screen(100, 200) and f.contains_screen(1099, 899)
    assert not f.contains_screen(1100, 500) and not f.contains_screen(99, 500) and not f.contains_screen(500, 900)
