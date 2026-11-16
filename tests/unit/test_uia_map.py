from hands.desktop.coords import Frame
from hands.desktop.uia import rows_to_elements

F = Frame(100, 200, 1000, 600, 1.25)       # CSS 视口 800 × 480


def row(ct, name, rect, aria="", value="", pw=False, enabled=True):
    return {"ct": ct, "name": name, "rect": rect, "aria": aria, "value": value, "pw": pw, "enabled": enabled,
            "rid": (42, len(name)), "_e": None}


ROWS = [
    row(50030, "页面", (100, 200, 1100, 800), aria="document", value="http://x/"),
    row(50007, "", (100, 250, 1100, 350), aria="listitem"),
    row(50005, "第一条结果", (125, 260, 325, 285), aria="link", value="http://x/a"),
    row(50020, "第一条结果", (125, 260, 325, 285)),
    row(50020, "摘要文字 便宜好吃", (125, 290, 600, 310)),
    row(50004, "搜索", (125, 210, 425, 235), aria="searchbox", value="寿司"),
    row(50004, "密码", (125, 400, 425, 425), aria="textbox", value="secret", pw=True),
    row(50000, "按钮", (125, 1200, 200, 1230), aria="button"),        # 视口外
    row(50000, "", (0, 0, 0, 0), aria="button"),                       # 不可见
    row(50020, "页脚说明", (125, 700, 300, 720)),
]


def test_roles_fields_and_ids():
    els, body = rows_to_elements(ROWS, F, epoch=3)
    assert [e.id for e in els] == [f"e{i}" for i in range(1, len(els) + 1)]
    roles = [e.role for e in els]
    assert roles == ["link", "searchbox", "textbox", "button", "button"]   # Document、Text、ListItem 不进清单
    link, search, pw, off, hidden = els
    assert link.href == "http://x/a" and link.value is None and link.source == "uia"
    assert (link.box.x, link.box.y, link.box.width, link.box.height) == (20.0, 48.0, 160.0, 20.0)
    assert link.in_viewport and "便宜好吃" in link.context
    assert search.value == "寿司" and search.tag == "input"
    assert pw.is_password and pw.value is None and pw.to_dict()["value"] is None
    assert off.visible and not off.in_viewport
    assert not hidden.visible
    assert link._ref.epoch == 3


def test_body_text_in_document_order():
    _, body = rows_to_elements(ROWS, F)
    assert body.splitlines() == ["第一条结果", "摘要文字 便宜好吃", "页脚说明"]
