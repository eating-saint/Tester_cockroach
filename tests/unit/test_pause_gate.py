import asyncio
import time

import pytest

from hands.pause import Interrupted, PauseGate


def test_human_input_pauses_and_check_raises():
    g = PauseGate()
    g.check()
    g.on_human_input()
    assert g.is_paused and g.paused.reason == "human_input"
    with pytest.raises(Interrupted):
        g.check()
    assert g.resume("button")
    assert not g.is_paused and g.log[-1]["how"] == "button"


def test_grace_period_and_watch_off():
    g = PauseGate()
    g.arm(10)
    g.on_human_input()
    assert not g.is_paused
    g2 = PauseGate(watch_human=False)
    g2.on_human_input()
    assert not g2.is_paused
    g2.pause("user")
    assert g2.is_paused


async def test_wait_returns_paused_seconds():
    g = PauseGate()
    assert await g.wait() == 0.0
    g.pause("user")

    async def later():
        await asyncio.sleep(0.2)
        g.resume("cli")
    asyncio.get_running_loop().create_task(later())
    s = await g.wait()
    assert 0.15 <= s <= 1.0


async def test_idle_auto_resume():
    g = PauseGate(idle_resume_s=0.2)
    g.on_human_input()
    assert g.is_paused
    t = time.monotonic()
    await g.wait()
    assert time.monotonic() - t >= 0.15
    assert g.log[-1]["how"] == "idle"


async def test_idle_extends_while_human_keeps_moving():
    g = PauseGate(idle_resume_s=0.3)
    g.on_human_input()
    for _ in range(3):
        await asyncio.sleep(0.15)
        g.on_human_input()              # 人还在动：不应恢复
        assert g.is_paused
    await g.wait()
    assert g.log[-1]["how"] == "idle"
