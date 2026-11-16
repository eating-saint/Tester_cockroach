"""桌面驱动与关联展开一键验收（自动项）。

python run_desktop_tests.py                 # 全部自动项（会真实移动鼠标：运行期间勿碰电脑）
python run_desktop_tests.py --only d12,d17  # 只跑部分
python run_desktop_tests.py --cdp-only      # 只跑不动鼠标的部分（CDP 驱动下的关联展开）

人工项（d08 人一碰就停、125% 缩放、d20–d22 真实网站）见 docs/desktop/checklist.md，在阶段六由用户在场执行。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACC = ROOT / "tests" / "acceptance"

SUITES = [
    ("unit", "单元测试", [sys.executable, "-m", "pytest", "-q"], False),
    ("d12", "关联展开·变形金刚（CDP）", [sys.executable, str(ACC / "d12_expand.py"), "--driver", "cdp"], False),
    ("d17", "关联展开·点餐（CDP）", [sys.executable, str(ACC / "d17_dinner.py"), "--driver", "cdp"], False),
    ("d12d", "关联展开·变形金刚（桌面，保留上限 3）",
     [sys.executable, str(ACC / "d12_expand.py"), "--driver", "desktop", "--keep-tabs", "3"], True),
    ("d17d", "关联展开·点餐（桌面）", [sys.executable, str(ACC / "d17_dinner.py"), "--driver", "desktop"], True),
    ("ddesk", "桌面驱动行为 C3–C6、C9、C10", [sys.executable, str(ACC / "d_desktop.py")], True),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--cdp-only", action="store_true")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    out = ROOT / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-desktop-acceptance"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for key, title, cmd, moves_mouse in SUITES:
        if only and key not in only:
            continue
        if a.cdp_only and moves_mouse:
            continue
        print(f"[{key}] {title} 运行中……", flush=True)
        t0 = time.monotonic()
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dt = time.monotonic() - t0
        (out / f"{key}.log").write_text(p.stdout + "\n" + p.stderr, encoding="utf-8")
        tail = [ln for ln in p.stdout.splitlines() if ln.strip()][-1:] or [""]
        rows.append({"key": key, "title": title, "ok": p.returncode == 0, "seconds": round(dt, 1), "tail": tail[0]})
        print(f"[{key}] {'通过' if p.returncode == 0 else '失败'} {dt:.0f}s  {tail[0]}", flush=True)
    (out / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(r["ok"] for r in rows)
    print(f"\n通过 {ok} / {len(rows)}    日志：{out}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
