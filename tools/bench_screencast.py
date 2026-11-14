"""推流基准测试（U2 / AC5）：python tools/bench_screencast.py [--seconds 10]

无界面启动自动化 Chrome，打开持续动画页，对三档画质各推流 N 秒，统计帧率和延迟中位数
（收到帧的时刻 − 帧自带的时间戳），打印表格并写入 runs/bench_screencast.json。
把最高帧率填进 spec N5：标准 = 该值 × 90%。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fixtures"))

from hands.browser import BrowserManager, profile_chrome_pids  # noqa: E402
from hands.config import load_config  # noqa: E402

LEVELS = [(70, 1280, 800), (60, 960, 600), (50, 640, 400)]
PORT = 8765


async def bench(seconds: float) -> list[dict]:
    import server as fixture_server
    srv = fixture_server.start(PORT)
    cfg = load_config(headless=True)
    b = BrowserManager(cfg)
    await b.start()
    out = []
    try:
        page = await b.new_page()
        await page.navigate(f"http://127.0.0.1:{PORT}/panel_bench/index.html")
        s = page.session
        for q, w, h in LEVELS:
            times, lats = [], []

            def on_frame(p):
                now = time.time()
                times.append(time.monotonic())
                ts = p.get("metadata", {}).get("timestamp")
                if ts:
                    lats.append(now - ts)
                asyncio.ensure_future(s.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))
            b.conn.on("Page.screencastFrame", on_frame, s.session_id)
            await s.send("Page.startScreencast", {"format": "jpeg", "quality": q, "maxWidth": w, "maxHeight": h})
            await asyncio.sleep(seconds)
            await s.send("Page.stopScreencast")
            b.conn.off("Page.screencastFrame", on_frame, s.session_id)
            span = (times[-1] - times[0]) if len(times) > 1 else seconds
            fps = (len(times) - 1) / span if len(times) > 1 else 0
            out.append({"quality": q, "max_width": w, "max_height": h, "frames": len(times),
                        "fps": round(fps, 1), "latency_ms_p50": round(statistics.median(lats) * 1000, 1) if lats else None})
            await asyncio.sleep(0.5)
    finally:
        await b.stop()
        srv.stop()
    left = profile_chrome_pids(cfg.profile_dir)
    if left:
        print(f"警告：残留 Chrome 进程 {left}")
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10)
    a = ap.parse_args()
    rows = asyncio.run(bench(a.seconds))
    print(f"{'画质':<6}{'最大尺寸':<12}{'帧数':<8}{'帧率':<8}延迟中位数(ms)")
    for r in rows:
        print(f"{r['quality']:<8}{r['max_width']}x{r['max_height']:<8}{r['frames']:<8}{r['fps']:<10}{r['latency_ms_p50']}")
    best = max(r["fps"] for r in rows)
    print(f"最高帧率 {best} 帧/秒 → AC5 标准 = {best * 0.9:.1f} 帧/秒")
    out = load_config().runs_dir / "bench_screencast.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "levels": rows, "best_fps": best,
                               "ac5_threshold_fps": round(best * 0.9, 1)}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"已写入 {out}")


if __name__ == "__main__":
    main()
