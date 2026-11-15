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


DRIVERS = ("cdp", "playwright")
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
    if not cfg.chrome_path:
        cfg.chrome_path = find_chrome()
    return cfg
