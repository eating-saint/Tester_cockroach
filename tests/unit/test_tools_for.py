import json

import pytest

from hands.decision import DecisionInvalid, tools_for, validate_decision
from hands.driver import DESKTOP_CAPS, Capabilities


def raw(tool, **args):
    return json.dumps({"decision": "call_tool", "reason": "r", "tool": tool, "arguments": args})


def test_default_has_no_desktop_tools():
    t = tools_for(None)
    assert "right_click" not in t and "show_grid" not in t and "click" in t
    assert tools_for(Capabilities()) == t


def test_desktop_tools():
    t = tools_for(DESKTOP_CAPS)
    assert {"right_click", "show_grid", "click", "extract"} <= set(t)


def test_validate_respects_driver():
    with pytest.raises(DecisionInvalid, match="not supported"):
        validate_decision(raw("right_click", element="e1"))
    d = validate_decision(raw("right_click", element="e1"), tools=tools_for(DESKTOP_CAPS))
    assert d.tool == "right_click"
    assert validate_decision(raw("show_grid"), tools=tools_for(DESKTOP_CAPS)).tool == "show_grid"
    assert validate_decision(raw("click", element="e1")).tool == "click"
