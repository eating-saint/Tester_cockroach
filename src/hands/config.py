"""全局配置：从 config.yaml 读取，缺项用默认值。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CAPTCHA_MARKERS = [
    "captcha", "challenge", "recaptcha", "hcaptcha", "turnstile",
    "verify you are human", "验证您是人类", "人机验证",
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


def find_chrome() -> str:
    candidates = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    raise FileNotFoundError("chrome.exe not found in: " + ", ".join(map(str, candidates)))


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


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
    if not cfg.chrome_path:
        cfg.chrome_path = find_chrome()
    return cfg
