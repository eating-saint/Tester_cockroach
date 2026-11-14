"""F12 人工介入：检测验证码/人机验证，切到有界面让人工处理后继续。"""
from __future__ import annotations

import asyncio
import inspect
import sys
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
        # 不可见 / 0 尺寸的 iframe（如广告带进来的 reCAPTCHA 后台框架 recaptcha/api2/aframe）不是要人处理的验证框
        box = getattr(e, "box", None)
        if not getattr(e, "visible", True) or (box is not None and box.width * box.height <= 0):
            continue
        hay = f"{e.name} {e.href or ''}".lower()
        for m in ms:
            if m in hay:
                return f"iframe:{m}"
    return None


def signature(state, hit: str) -> tuple[str, str]:
    """已处理验证码的签名：(网址去掉 # 之后的部分, 命中标记)。真实验证框通过后 iframe 仍在页面上，
    同签名不再重复转人工。"""
    return ((state.url or "").split("#", 1)[0], hit)


def _console_interactive() -> bool:
    try:
        return sys.stdin is not None and not sys.stdin.closed and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


async def wait_cleared(browser, markers=None, log=print, timeout_s: float = 900, interval_s: float = 2.0) -> bool:
    """轮询当前页面，连续两次检测不到验证码就返回 True（人工已处理完）；超时返回 False。"""
    from types import SimpleNamespace

    from .config import DEFAULT_CAPTCHA_MARKERS
    from .dom import DomReader

    markers = markers or DEFAULT_CAPTCHA_MARKERS
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    clear = 0
    while loop.time() < deadline:
        await asyncio.sleep(interval_s)  # condition-poll
        page = browser.page
        if page is None:
            continue
        try:
            els, body = await DomReader().read(page)
            url = await page.url()
        except Exception:
            clear = 0                    # 页面正在跳转，下一轮再看
            continue
        if detect(SimpleNamespace(url=url, body_text=body, elements=els), markers) is None:
            clear += 1
            if clear >= 2:
                log("[HUMAN_REQUIRED] 验证码已消失，继续。")
                return True
        else:
            clear = 0
    log(f"[HUMAN_REQUIRED] 等待人工超时（{int(timeout_s)} 秒），继续执行。")
    return False


async def handoff(browser, url: str, save_dir, input_fn=input, log=print, markers=None):
    """保存截图 → 有界面打开同一网址 → 等人工处理（控制台回车；没有可交互控制台时自动检测验证码消失）
    → 回到原显示方式打开同一网址。返回新的 Page。"""
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
    if inspect.iscoroutinefunction(input_fn):
        await input_fn(page)          # 测试用：模拟人工在有界面窗口里的操作
    elif input_fn is input and not _console_interactive():
        log("[HUMAN_REQUIRED] 请在弹出的浏览器窗口中完成验证。没有可交互的控制台：验证码消失后自动继续……")
        await wait_cleared(browser, markers, log)
    else:
        log("[HUMAN_REQUIRED] 请在弹出的浏览器窗口中完成验证，完成后回到这里按回车继续……")
        try:
            await asyncio.to_thread(input_fn)
        except EOFError:              # 控制台读不到输入（例如后台运行）：改为自动检测
            log("[HUMAN_REQUIRED] 控制台无法输入：改为自动检测，验证码消失后自动继续……")
            await wait_cleared(browser, markers, log)
    await browser.relaunch(headless=was_headless)
    page = await browser.new_page()
    await page.navigate(url)
    return page
