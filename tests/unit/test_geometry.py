from hands.geometry import Box, accumulate_offset, css_to_image, quad_to_box


def test_center_contains():
    b = Box(10, 20, 100, 50)
    assert b.center() == (60, 45)
    assert b.contains(10, 20) and b.contains(110, 70)
    assert not b.contains(111, 45)


def test_offset_intersects():
    b = Box(0, 0, 10, 10).offset(5, 5)
    assert (b.x, b.y) == (5, 5)
    assert b.intersects(Box(0, 0, 6, 6))
    assert not b.intersects(Box(20, 20, 5, 5))


def test_quad_to_box():
    assert quad_to_box([1, 2, 11, 2, 11, 7, 1, 7]) == Box(1, 2, 10, 5)


def test_css_to_image():
    assert css_to_image(Box(8, 4, 100, 40), 1.25) == Box(10, 5, 125, 50)


def test_accumulate_offset():
    assert accumulate_offset([(1, 2), (3, 4)]) == (4, 6)
    assert accumulate_offset([]) == (0, 0)


def test_to_dict():
    assert Box(1.234, 2, 3, 4).to_dict()["x"] == 1.23
