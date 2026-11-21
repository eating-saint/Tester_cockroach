# PyInstaller 配置：pyinstaller packaging/panel.spec
# 产物：dist/HandsPanel/HandsPanel.exe（onedir）。数据目录在程序旁边的 data/，升级只替换程序文件。
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"

datas = [
    (str(SRC / "panel" / "static"), "panel/static"),
    (str(ROOT / "config.yaml"), "seed"),
    (str(ROOT / "cases"), "seed/cases"),
    (str(ROOT / "fixtures"), "seed/fixtures"),
    (str(SRC / "hands" / "vlm" / "prompts"), "hands/vlm/prompts"),     # ver1 提示词（程序自带；可用 vlm.prompts_dir 指向副本）
    (str(ROOT / "compare" / "sets"), "seed/compare/sets"),             # ver1 人眼比对集
]
hidden = collect_submodules("aiohttp") + collect_submodules("webview") + ["webview.platforms.winforms",
                                                                          "webview.platforms.edgechromium"]

a = Analysis(
    [str(ROOT / "packaging" / "panel_entry.py")],
    pathex=[str(SRC)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "pytest", "jedi", "IPython", "parso"],   # jedi/IPython 是开发环境带入的（约 15 MB）
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HandsPanel", console=False,
          icon=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="HandsPanel")
