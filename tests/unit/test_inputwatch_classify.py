import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_classify():
    from hands.desktop import win32
    from hands.desktop.inputwatch import classify
    assert classify(True, win32.INJECT_MARK) == "self"
    assert classify(False, 0) == "human"
    assert classify(True, 0) == "other"
    assert classify(False, 12345) == "human"
