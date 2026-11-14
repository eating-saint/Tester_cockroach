"""手动输入转发：面板预览里的鼠标、滚轮、键盘 → Input.dispatch*（直接送达，不经过轨迹模块）。"""
from __future__ import annotations

SPECIAL_VK = {
    "Enter": 13, "Tab": 9, "Escape": 27, "Backspace": 8, "Delete": 46, "Insert": 45,
    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
    "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34, " ": 32,
    "Shift": 16, "Control": 17, "Alt": 18, "Meta": 91, "CapsLock": 20,
    **{f"F{i}": 111 + i for i in range(1, 13)},
}
PUNCT_VK = {";": 186, "=": 187, ",": 188, "-": 189, ".": 190, "/": 191, "`": 192, "[": 219, "\\": 220,
            "]": 221, "'": 222}
BUTTONS = {0: "left", 1: "middle", 2: "right"}


def modifiers(m: dict | None) -> int:
    m = m or {}
    return (1 if m.get("alt") else 0) | (2 if m.get("ctrl") else 0) | (4 if m.get("meta") else 0) | \
        (8 if m.get("shift") else 0)


def vk_of(key: str, code: str = "") -> int:
    if key in SPECIAL_VK:
        return SPECIAL_VK[key]
    if len(key) == 1:
        c = key.upper()
        if "A" <= c <= "Z" or "0" <= c <= "9":
            return ord(c)
        if key in PUNCT_VK:
            return PUNCT_VK[key]
    if code.startswith("Key") and len(code) == 4:
        return ord(code[3])
    if code.startswith("Digit") and len(code) == 6:
        return ord(code[5])
    return 0


class InputForwarder:
    def __init__(self):
        self._buttons = 0
        self._click_count = 0

    async def handle(self, session, msg: dict):
        t = msg.get("t")
        if t == "mouse":
            await self._mouse(session, msg)
        elif t == "key":
            await self._key(session, msg)
        elif t == "text":
            text = msg.get("text") or ""
            if text:
                await session.send("Input.insertText", {"text": text})

    async def _mouse(self, s, m):
        x, y = float(m.get("x", 0)), float(m.get("y", 0))
        kind = m.get("kind")
        mods = modifiers(m.get("mods"))
        btn = BUTTONS.get(int(m.get("button", 0)), "left")
        if kind == "move":
            await s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "modifiers": mods,
                                                      "buttons": self._buttons,
                                                      "button": btn if self._buttons else "none"})
        elif kind == "down":
            self._click_count = int(m.get("detail", 1) or 1)
            self._buttons |= {"left": 1, "right": 2, "middle": 4}[btn]
            await s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": btn,
                                                      "clickCount": self._click_count, "modifiers": mods,
                                                      "buttons": self._buttons})
        elif kind == "up":
            self._buttons &= ~{"left": 1, "right": 2, "middle": 4}[btn]
            await s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": btn,
                                                      "clickCount": self._click_count or 1, "modifiers": mods,
                                                      "buttons": self._buttons})
        elif kind == "wheel":
            await s.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "modifiers": mods,
                                                      "deltaX": float(m.get("dx", 0)), "deltaY": float(m.get("dy", 0))})

    async def _key(self, s, m):
        key, code = m.get("key", ""), m.get("code", "")
        mods = modifiers(m.get("mods"))
        vk = vk_of(key, code)
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": mods}
        if m.get("kind") == "up":
            await s.send("Input.dispatchKeyEvent", {"type": "keyUp", **base})
            return
        printable = len(key) == 1 and not (mods & (1 | 2 | 4))
        if key == "Enter":
            await s.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": "\r", **base})
        elif printable:
            await s.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": key, **base})
        else:
            down = {"type": "rawKeyDown", **base}
            if mods & 2 and key.lower() in ("a", "c", "v", "x", "z"):
                down["commands"] = [{"a": "selectAll", "c": "copy", "v": "paste", "x": "cut",
                                     "z": "undo"}[key.lower()]]
            await s.send("Input.dispatchKeyEvent", down)
