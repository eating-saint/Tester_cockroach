"""F12 人工介入：检测验证码/人机验证，切到有界面让人工处理后继续。"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path


def detect(state, markers: list[str]) -> str | None:
    ms = [m.lower() for m in markers]
    url = (state.url or "").lower()
    for m in ms:
        if m in url:
            return f"url:{m}"
    # 正文只匹配短语类标记（含空格或非 ASCII），避免 "challenge" 这类普通单词在搜索结果里误报
    body = (state.body_text or "").lower()
    for m in ms:
        if (" " in m or not m.isascii()) and m in body:
            return f"text:{m}"
    for e in state.elements:
        if e.tag.lower() != "iframe":
            continue
        hay = f"{e.name} {e.href or ''}".lower()
        for m in ms:
            if m in hay:
                return f"iframe:{m}"
    return None


async def handoff(browser, url: str, save_dir, input_fn=input, log=print):
    """保存截图 → 有界面打开同一网址 → 等人工回车 → 回到无头打开同一网址。返回新的 Page。"""
    from . import screenshot as shot

    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    shot_path = save_dir / "captcha.png"
    try:
        shot_path.write_bytes(await shot.capture(browser.page))
    except Exception as e:  # 截图失败不阻断人工流程
        log(f"[HUMAN_REQUIRED] 截图失败: {e}")
    was_headless = browser.headless
    await browser.relaunch(headless=False)
    page = await browser.new_page()
    await page.navigate(url)
    log(f"[HUMAN_REQUIRED] 检测到验证码/人机验证。截图: {shot_path}")
    log("[HUMAN_REQUIRED] 请在弹出的浏览器窗口中完成验证，完成后回到这里按回车继续……")
    if inspect.iscoroutinefunction(input_fn):
        await input_fn(page)          # 测试用：模拟人工在有界面窗口里的操作
    else:
        await asyncio.to_thread(input_fn)
    await browser.relaunch(headless=was_headless)
    page = await browser.new_page()
    await page.navigate(url)
    return page
