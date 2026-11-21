"""已配对设备与配对码（平板远程访问 spec F11）。

- 配对码：secrets.token_urlsafe(32)，5 分钟有效、用一次即作废，绑定签发时的访问地址（局域网 / 本次公网）。
- 设备凭证：随机 32 字节，明文只在配对那一次下发给平板（HttpOnly cookie），电脑上只存 sha256 摘要。
- 局域网设备写盘（长期有效）；公网设备只在内存（隧道关闭即作废，不落盘）。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

CODE_TTL_S = 300
SEEN_WRITE_S = 60            # 最近访问时间写盘的节流


@dataclass
class Device:
    id: str
    name: str
    origin: str                  # 绑定的访问地址
    kind: str                    # lan / public
    secret_hash: str
    created: float
    last_seen: float

    def public(self) -> dict:
        d = asdict(self)
        d.pop("secret_hash")
        return d


@dataclass
class PairCode:
    code: str
    origin: str
    kind: str
    expires: float
    used: bool = False


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def browser_name(ua: str) -> str:
    ua = ua or ""
    if "Edg/" in ua:
        return "Edge"
    if "Firefox/" in ua or "FxiOS" in ua:
        return "Firefox"
    if "CriOS" in ua or ("Chrome/" in ua and "Chromium" not in ua):
        return "Chrome"
    if "Safari/" in ua:
        return "Safari"
    return "浏览器"


def device_label(ua: str) -> str:
    ua = ua or ""
    phone = "iPhone" in ua or ("Android" in ua and "Mobile" in ua)
    return f"{'手机' if phone else '平板'} · {browser_name(ua)}"


class DeviceStore:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.devices: dict[str, Device] = {}
        self.codes: dict[str, PairCode] = {}            # origin → 当前有效的配对码
        self._written = 0.0
        self._load()

    # ---------------- 存取 ----------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in data.get("devices", []):
            try:
                dev = Device(**d)
            except TypeError:
                continue
            if dev.kind == "lan":
                self.devices[dev.id] = dev

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(d) for d in self.devices.values() if d.kind == "lan"]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        self._written = self.clock()

    # ---------------- 配对码 ----------------
    def issue(self, origin: str, kind: str) -> PairCode:
        pc = PairCode(secrets.token_urlsafe(32), origin, kind, self.clock() + CODE_TTL_S)
        self.codes[origin] = pc
        return pc

    def current_code(self, origin: str, kind: str) -> PairCode:
        """当前有效的配对码；过期或用过就换新。"""
        pc = self.codes.get(origin)
        if pc is None or pc.used or pc.expires <= self.clock():
            pc = self.issue(origin, kind)
        return pc

    def redeem(self, code: str, origin: str, ua: str = "") -> tuple[Device, str]:
        """平板扫码后兑换：返回 (设备, 凭证明文)。码不存在 / 已用 / 过期 / 地址不同 → PermissionError。"""
        pc = next((c for c in self.codes.values() if secrets.compare_digest(c.code, str(code or ""))), None)
        if pc is None:
            raise PermissionError("配对码无效：请在电脑上刷新二维码再扫")
        if pc.used:
            raise PermissionError("配对码已经用过：请在电脑上刷新二维码再扫")
        if pc.expires <= self.clock():
            raise PermissionError("配对码已过期：请在电脑上刷新二维码再扫")
        if pc.origin != origin:
            raise PermissionError("配对码不是这个地址的：请扫电脑上对应地址的二维码")
        pc.used = True
        secret = secrets.token_urlsafe(32)
        now = self.clock()
        dev = Device(secrets.token_hex(8), device_label(ua), origin, pc.kind, _hash(secret), now, now)
        self.devices[dev.id] = dev
        if dev.kind == "lan":
            self._save()
        return dev, secret

    # ---------------- 设备 ----------------
    def verify(self, cookie: str | None, origin: str | None = None) -> Device | None:
        """cookie = "<设备id>.<凭证>"。对上就更新最近访问时间。origin 给出时还要求与配对时的地址一致。"""
        if not cookie or "." not in cookie:
            return None
        did, _, secret = cookie.partition(".")
        dev = self.devices.get(did)
        if dev is None or not secrets.compare_digest(dev.secret_hash, _hash(secret)):
            return None
        if origin is not None and dev.origin != origin:
            return None
        dev.last_seen = self.clock()
        if dev.kind == "lan" and self.clock() - self._written >= SEEN_WRITE_S:
            self._save()
        return dev

    def rename(self, did: str, name: str) -> Device:
        dev = self.devices.get(did)
        if dev is None:
            raise KeyError(did)
        dev.name = (name or "").strip()[:40] or dev.name
        if dev.kind == "lan":
            self._save()
        return dev

    def revoke(self, did: str) -> Device | None:
        dev = self.devices.pop(did, None)
        if dev is not None and dev.kind == "lan":
            self._save()
        return dev

    def purge(self, kind: str = "public") -> list[Device]:
        gone = [d for d in self.devices.values() if d.kind == kind]
        for d in gone:
            self.devices.pop(d.id, None)
        if kind == "public":
            self.codes = {o: c for o, c in self.codes.items() if c.kind != "public"}
        else:
            self._save()
        return gone

    def list(self) -> list[dict]:
        return [d.public() for d in sorted(self.devices.values(), key=lambda d: d.created)]
