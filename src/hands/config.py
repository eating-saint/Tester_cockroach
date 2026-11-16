"""全局配置：从 config.yaml 读取，缺项用默认值。"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 数据目录：配置、用户目录、运行记录、轨迹库都相对它。打包后由环境变量指向 <程序目录>/data，
# 开发时不设置，和以前一样就是项目根目录。
DATA_ROOT = Path(os.environ["HANDS_DATA_DIR"]).resolve() if os.environ.get("HANDS_DATA_DIR") else PROJECT_ROOT

DEFAULT_CAPTCHA_MARKERS = [
    "captcha", "challenge", "recaptcha", "hcaptcha", "turnstile",
    "verify you are human", "验证您是人类", "人机验证",
    "challenges.cloudflare.com",
    "robot or human", "human challenge", "人工验证挑战",   # PerimeterX 类“按住按钮”验证（如 Walmart）
]


@dataclass
class Config:
    chrome_path: str = ""
    profile_dir: Path = Path("profiles/ver0")
    headless: bool = True
    window: tuple[int, int] = (1280, 800)
    quiet_ms: int = 500
    nav_timeout_s: float = 30
    cmd_timeout_s: float = 30
    watchdog_interval_s: float = 0.5
    max_crash_reruns: int = 2
    min_interval_s: dict = field(default_factory=lambda: {"bing.com": 20})
    captcha_markers: list = field(default_factory=lambda: list(DEFAULT_CAPTCHA_MARKERS))
    runs_dir: Path = Path("runs")
    humanize: dict = field(default_factory=dict)   # 鼠标轨迹与干扰项，见 humanize.py；默认全部关闭
    driver: str = "cdp"                            # cdp = 自写原生 CDP；playwright = Playwright（可选依赖 pw）
    decision: str = "rule"                         # rule = 规则决策；vlm = 视觉大模型决策（ver1）
    vlm: dict = field(default_factory=dict)        # 大模型设置，缺项用 VLM_DEFAULTS
    guard: dict = field(default_factory=dict)      # 兜底拦截清单增删：add_words / remove_words / add_paths / remove_paths
    desktop: dict = field(default_factory=dict)    # 桌面驱动设置，缺项用 DESKTOP_DEFAULTS
    confirm: dict = field(default_factory=dict)    # 需确认：enabled（None = 桌面开、其他关）、各组词增删


DRIVERS = ("cdp", "playwright", "desktop")

DESKTOP_DEFAULTS = {
    "window": [100, 60, 1280, 860],   # 代理窗口屏幕位置与大小（物理像素）：x, y, w, h
    "stable_ms": 600,                 # 截图保持不变多久算稳定
    "settle_timeout_s": 20,           # 等待稳定的总超时
    "commit_wait_s": 1.0,             # 动作后观察网址是否变化的时长
    "uia_timeout_s": 5,               # 读一次无障碍树的超时
    "grid": {"cols": 12, "rows": 8, "min_elements": 3},
    "wheel_px_per_notch": 100,        # 一格滚轮大约滚动多少 CSS 像素
    "idle_resume_s": 0,               # 人停手 N 秒自动恢复；0 = 只能手动恢复
    "resume_grace_s": 2,              # 恢复后宽限期：期内的人工输入不触发暂停
    "thumb_interval_s": 1.0,          # 面板缩略预览间隔
    # 真实鼠标的移动速度：桌面驱动默认走平滑轨迹，慢一点让人看得清（humanize.mouse 没开时也生效）
    "mouse": {"min_duration_ms": 800, "max_duration_ms": 1600},
    "click_pause_ms": 300,            # 鼠标到位后、按下前停一下
}


def desktop_settings(cfg: "Config") -> dict:
    """桌面驱动设置：默认值合并 config.yaml 里写的值（grid 段逐项合并）。"""
    user = dict(cfg.desktop or {})
    grid = {**DESKTOP_DEFAULTS["grid"], **(user.pop("grid", None) or {})}
    mouse = {**DESKTOP_DEFAULTS["mouse"], **(user.pop("mouse", None) or {})}
    return {**DESKTOP_DEFAULTS, **user, "grid": grid, "mouse": mouse}


def desktop_humanize(config: "Config", hcfg):
    """桌面驱动：鼠标默认走平滑轨迹，速度取 desktop.mouse（让人看得清）。返回新的 HumanizeConfig。"""
    import copy
    h = copy.deepcopy(hcfg)
    m = desktop_settings(config)["mouse"]
    h.mouse.enabled = True
    h.mouse.min_duration_ms = int(m["min_duration_ms"])
    h.mouse.max_duration_ms = int(m["max_duration_ms"])
    return h
DECISIONS = ("rule", "vlm")

VLM_DEFAULTS = {
    "base_url": "http://127.0.0.1:11434",
    "model": "qwen3.5:4b",
    "timeout_s": 120,
    "num_ctx": 16384,
    "temperature": 0.2,
    "seed": 1234,
    "keep_alive": "10m",
    "max_side": 1280,            # 截图缩放后的长边（像素）
    "offscreen_top_k": 40,       # 视口外最多列出几个元素
    "body_chars": 1500,          # 正文摘要长度
    "redescribe_scroll": 1.0,    # 视口滚动超过几屏就重新描述
    "redescribe_change": 0.5,    # 元素集合相似度低于它就重新描述
    "prompts_dir": None,         # None = 用程序自带的提示词
}


def vlm_settings(cfg: "Config") -> dict:
    """大模型设置：默认值合并用户在 config.yaml 里写的值。"""
    return {**VLM_DEFAULTS, **(cfg.vlm or {})}


def find_chrome() -> str:
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    if sys.platform != "win32":              # 非 Windows（例如 CI 或 Linux 机器）
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                return found
    raise FileNotFoundError("chrome.exe not found in: " + ", ".join(map(str, candidates)))


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (DATA_ROOT / p)


def load_config(path: str | os.PathLike | None = "config.yaml", **overrides) -> Config:
    data: dict = {}
    if path is not None:
        p = _abs(path)
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.update({k: v for k, v in overrides.items() if v is not None})
    known = {f.name for f in fields(Config)}
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    cfg.profile_dir = _abs(cfg.profile_dir)
    cfg.runs_dir = _abs(cfg.runs_dir)
    cfg.window = tuple(cfg.window)
    cfg.driver = str(cfg.driver or "cdp").lower()
    if cfg.driver not in DRIVERS:
        raise ValueError(f"未知驱动 driver={cfg.driver!r}，可选：{' / '.join(DRIVERS)}")
    cfg.decision = str(cfg.decision or "rule").lower()
    if cfg.decision not in DECISIONS:
        raise ValueError(f"未知决策方式 decision={cfg.decision!r}，可选：{' / '.join(DECISIONS)}")
    cfg.vlm = dict(cfg.vlm or {})
    cfg.guard = dict(cfg.guard or {})
    cfg.desktop = dict(cfg.desktop or {})
    cfg.confirm = dict(cfg.confirm or {})
    if not cfg.chrome_path:
        cfg.chrome_path = find_chrome()
    return cfg
