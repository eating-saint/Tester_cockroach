import json

import pytest

from panel.devices import CODE_TTL_S, DeviceStore, browser_name, device_label

LAN = "http://192.168.1.5:8791"
PUB = "https://abc-def.trycloudflare.com"
SAFARI_IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
CHROME_TAB = "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_names():
    assert browser_name(SAFARI_IPAD) == "Safari" and browser_name(CHROME_TAB) == "Chrome"
    assert device_label(SAFARI_IPAD) == "平板 · Safari" and device_label(CHROME_TAB) == "平板 · Chrome"


def test_redeem_rules(tmp_path):
    c = Clock()
    s = DeviceStore(tmp_path / "d.json", c)
    pc = s.issue(LAN, "lan")
    assert len(pc.code) >= 40
    with pytest.raises(PermissionError, match="不是这个地址"):
        s.redeem(pc.code, PUB)
    dev, secret = s.redeem(pc.code, LAN, SAFARI_IPAD)
    assert dev.kind == "lan" and dev.name == "平板 · Safari"
    with pytest.raises(PermissionError, match="用过"):
        s.redeem(pc.code, LAN)
    pc2 = s.issue(LAN, "lan")
    c.t += CODE_TTL_S + 1
    with pytest.raises(PermissionError, match="过期"):
        s.redeem(pc2.code, LAN)
    with pytest.raises(PermissionError, match="无效"):
        s.redeem("nope", LAN)
    assert s.current_code(LAN, "lan").code != pc2.code            # 过期自动换新


def test_verify_hash_only_and_persist(tmp_path):
    c = Clock()
    p = tmp_path / "d.json"
    s = DeviceStore(p, c)
    dev, secret = s.redeem(s.issue(LAN, "lan").code, LAN, CHROME_TAB)
    cookie = f"{dev.id}.{secret}"
    assert s.verify(cookie).id == dev.id and s.verify(cookie, LAN) is not None
    assert s.verify(cookie, PUB) is None and s.verify(f"{dev.id}.wrong") is None and s.verify(None) is None
    raw = p.read_text(encoding="utf-8")
    assert secret not in raw and json.loads(raw)["devices"][0]["secret_hash"]
    c.t += 100
    s.verify(cookie)
    s2 = DeviceStore(p, c)                     # 面板重启：局域网凭证仍有效
    assert s2.verify(cookie) is not None


def test_public_not_persisted_and_purge(tmp_path):
    c = Clock()
    p = tmp_path / "d.json"
    s = DeviceStore(p, c)
    lan, _ = s.redeem(s.issue(LAN, "lan").code, LAN)
    pub, psecret = s.redeem(s.issue(PUB, "public").code, PUB)
    assert pub.kind == "public"
    assert pub.id not in p.read_text(encoding="utf-8")
    gone = s.purge("public")
    assert [d.id for d in gone] == [pub.id] and s.verify(f"{pub.id}.{psecret}") is None
    assert [d["id"] for d in s.list()] == [lan.id] and "secret_hash" not in s.list()[0]
    assert PUB not in s.codes


def test_rename_revoke(tmp_path):
    s = DeviceStore(tmp_path / "d.json", Clock())
    dev, secret = s.redeem(s.issue(LAN, "lan").code, LAN)
    s.rename(dev.id, "客厅的平板")
    assert s.list()[0]["name"] == "客厅的平板"
    assert s.revoke(dev.id).id == dev.id and s.verify(f"{dev.id}.{secret}") is None
    assert DeviceStore(tmp_path / "d.json", Clock()).list() == []
