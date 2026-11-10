from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState
from hands.tasktree import TaskView


def el(eid, role="link", name="", text="", href=None, visible=True, tag="a"):
    return Element(eid, role, name, text or name, tag, href, None, Box(0, 0, 10, 10), visible, True, True)


def page(elements=(), url="http://x/", body=""):
    return PageState(url, "t", Box(0, 0, 1280, 800), 1.0, list(elements), body)


def view(goal="g", depth=0, children=(), recent=()):
    return TaskView("root" if depth == 0 else "n1", goal, "", depth, 0, 10, list(children), list(recent))
