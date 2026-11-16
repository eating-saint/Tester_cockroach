"""驱动选择：按 config.driver 创建 CDP 或 Playwright 浏览器；两套实现提供同一组方法（见下方协议）。

上层（runner、actions、captcha、面板）只依赖这些方法，不判断具体驱动。
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

from .cdp import BrowserCrashed
from .config import Config
from .geometry import Box


class DriverUnavailable(RuntimeError):
    """选择了某个驱动，但运行它所需的依赖没有安装。"""


class AgentWindowClosed(BrowserCrashed):
    """桌面驱动：代理窗口被关掉了。任务结束，不重跑（spec F2）。"""


PW_MISSING = ("已选择 Playwright 驱动，但未安装 playwright。请运行：pip install -e .[pw]"
              "（使用本机 Chrome，无需执行 playwright install）")
DESKTOP_MISSING = "已选择桌面驱动，但未安装 comtypes。请运行：pip install -e .[desktop]"
DESKTOP_NOT_WINDOWS = "桌面驱动只支持 Windows。请改用 CDP 或 Playwright 驱动（--driver cdp）"
DESKTOP_NOT_CHROME = ("桌面驱动要求默认浏览器是 Google Chrome（当前：{progid}）。"
                      "请在 Windows 设置 → 应用 → 默认应用 里把 Chrome 设为默认浏览器，或改用 CDP 驱动")


@dataclass(frozen=True)
class Capabilities:
    """驱动能力声明。上层只看这些开关，不判断具体驱动名。默认值即 CDP / Playwright 的现有行为。"""
    network_log: bool = True      # 有网络请求列表（等待超时能给出未完成请求）
    screencast: bool = True       # 有 CDP 会话，可推流、可在预览上手动输入
    js: bool = True               # 能在页面里执行脚本
    os_input: bool = False        # 动的是系统真实鼠标键盘（需要人工输入暂停）
    grid: bool = False            # 支持网格兜底（show_grid 工具）
    right_click: bool = False     # 支持右键菜单（right_click 工具）
    confirm_default: bool = False # 需确认判断默认开启

    def to_dict(self) -> dict:
        return asdict(self)


DESKTOP_CAPS = Capabilities(network_log=False, screencast=False, js=False, os_input=True,
                            grid=True, right_click=True, confirm_default=True)


def caps_of(obj) -> Capabilities:
    """浏览器或页面对象的能力；没有声明的按默认值。"""
    c = getattr(obj, "capabilities", None)
    return c if isinstance(c, Capabilities) else Capabilities()


@runtime_checkable
class PageLike(Protocol):
    driver: str
    monitor: Any

    async def navigate(self, url: str, timeout: float | None = None): ...
    async def reload(self, timeout: float | None = None): ...
    async def back(self, timeout: float | None = None): ...
    async def wait_settled(self, after_navigation: bool = False, timeout: float | None = None): ...
    def take_new_tab(self): ...
    async def adopt_new_tab(self, target): ...
    async def wait_for(self, getter, predicate, timeout: float, interval: float = 0.05): ...
    async def evaluate(self, expression: str): ...
    async def url(self) -> str: ...
    async def title(self) -> str: ...
    async def refresh_offsets(self): ...
    # 底层原语
    async def read_dom(self) -> tuple[list, str]: ...
    async def layout(self) -> dict: ...
    async def locate_box(self, el) -> Box: ...
    async def node_value(self, el) -> str | None: ...
    async def form_action(self, el) -> str | None: ...           # 元素所在表单的提交地址（只读；ver1 兜底拦截用）
    async def occluded(self, elements) -> set[str]: ...          # 中心点被别的元素挡住的元素 id（只读；ver1）
    async def mouse_move(self, x: float, y: float): ...
    async def mouse_click(self, x: float, y: float): ...
    async def mouse_wheel(self, x: float, y: float, dy: float): ...
    async def key_press(self, name: str): ...
    async def select_all(self): ...
    async def insert_text(self, text: str): ...
    async def screenshot(self, full_page: bool = False) -> bytes: ...
    async def bring_to_front(self): ...
    async def frame_info(self, el) -> tuple[tuple[float, float], bool]: ...
    async def evaluate_in_frame(self, el, expression: str): ...
    async def panel_session(self): ...
    # 可选方法（有就用，没有上层走原逻辑）：
    #   device_pixel_ratio() -> float            桌面驱动：显示缩放
    #   element_at(x, y) -> Element | None       桌面驱动：网格落点核对
    #   dialogs() -> list                        桌面驱动：系统对话框
    #   visual_hash() -> str                     桌面驱动：截图指纹，并入动作前后指纹
    #   new_tab(url) / select_tab(tab) / close_tab(tab)   关联展开保留标签页（三种驱动都实现）


@runtime_checkable
class BrowserLike(Protocol):
    driver: str
    config: Config
    headless: bool
    page: Any
    crashed: asyncio.Event
    crash_history: list
    crash_reason: str | None
    crash_detected_at: float | None

    @property
    def pid(self) -> int | None: ...
    async def start(self): ...
    async def stop(self): ...
    async def restart(self): ...
    async def relaunch(self, headless: bool): ...
    async def new_page(self): ...
    def is_alive(self) -> bool: ...


def create_browser(config: Config):
    if config.driver == "desktop":
        if sys.platform != "win32":
            raise DriverUnavailable(DESKTOP_NOT_WINDOWS)
        try:
            import comtypes  # noqa: F401
        except ImportError as e:
            raise DriverUnavailable(DESKTOP_MISSING) from e
        from .desktop.browser import DesktopBrowser
        return DesktopBrowser(config)
    if config.driver == "playwright":
        try:
            import playwright  # noqa: F401
        except ImportError as e:
            raise DriverUnavailable(PW_MISSING) from e
        from .pw.browser import PwBrowserManager
        return PwBrowserManager(config)
    from .browser import BrowserManager
    return BrowserManager(config)
