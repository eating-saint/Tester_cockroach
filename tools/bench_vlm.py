"""ver1 模型基准测试（AC0）：3 张夹具页各做 N 次描述调用与决策调用，记录耗时、token、显存，写 docs/ver1/bench.md。

用法：python tools/bench_vlm.py [--n 5] [--driver cdp|playwright] [--model qwen3.5:4b]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fixtures"))

from hands.config import load_config, vlm_settings  # noqa: E402
from hands.decision import DecisionInput  # noqa: E402
from hands.driver import create_browser  # noqa: E402
from hands.pagestate import build_page_state  # noqa: E402
from hands.tasktree import TaskTree  # noqa: E402
from hands.vlm.client import OllamaClient  # noqa: E402
from hands.vlm.decider import DECIDE_SCHEMA, VlmDecider  # noqa: E402
from hands.vlm.describe_cache import DESCRIBE_SCHEMA, REGION_KINDS, PageDescription  # noqa: E402
from hands.vlm.digest import PageDigest  # noqa: E402

PAGES = [("F1 静态页", "f01_static/index.html"), ("F17 仿电商", "f17_shop/index.html"),
         ("F19 密集元素", "f19_dense/index.html")]


def med(xs):
    return int(statistics.median(xs)) if xs else 0


async def main(args) -> int:
    import server as fixture_server
    cfg = load_config(driver=args.driver)
    s = vlm_settings(cfg)
    if args.model:
        s["model"] = args.model
    client = OllamaClient(s)
    await client.check()
    srv = fixture_server.start(8765)
    browser = create_browser(cfg)
    await browser.start()
    rows, vram = [], None
    try:
        await browser.new_page()
        page = browser.page
        tmp = Path(tempfile.mkdtemp(prefix="bench_vlm_"))
        dec = VlmDecider(cfg, client)
        for name, path in PAGES:
            await page.navigate(f"http://127.0.0.1:8765/{path}")
            d = tmp / path.split("/")[0]
            state = await build_page_state(page, d)
            png = (d / state.screenshot).read_bytes()
            digest = PageDigest.build(state, png, {"hints": ["搜索"]}, "找到搜索框", s)
            system = dec.pb.render("system")
            desc_user = dec.pb.render("describe", kinds="、".join(REGION_KINDS))
            ds, dt = [], []
            for i in range(args.n):
                r = await client.chat(system, desc_user, [digest.clean_png], DESCRIBE_SCHEMA)
                ds.append(r)
            inp = DecisionInput(state, TaskTree("找到搜索框并输入 spicy").view(), step_dir=d)
            user = dec._render_decide(inp, digest, PageDescription(regions=[], overlay=False))
            for i in range(args.n):
                r = await client.chat(system, user, [digest.annotated_png], DECIDE_SCHEMA)
                dt.append(r)
            vram = await client.vram() or vram
            rows.append({
                "page": name, "elements": len(digest.numbered),
                "desc_med": med([r.elapsed_ms for r in ds]), "desc_max": max(r.elapsed_ms for r in ds),
                "desc_in": med([r.prompt_tokens for r in ds]), "desc_out": med([r.output_tokens for r in ds]),
                "dec_med": med([r.elapsed_ms for r in dt]), "dec_max": max(r.elapsed_ms for r in dt),
                "dec_in": med([r.prompt_tokens for r in dt]), "dec_out": med([r.output_tokens for r in dt]),
            })
            print(rows[-1], flush=True)
    finally:
        await browser.stop()
        srv.stop()
    gpu = "未知"
    if vram and vram.get("size"):
        gpu = f"{vram['size_vram'] / 2**30:.2f} / {vram['size'] / 2**30:.2f} GB 在显卡上" + \
              ("（全部在显卡上）" if vram["size_vram"] >= vram["size"] else "（部分在内存里）")
    lines = [
        "# 模型基准测试（AC0）", "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　模型：`{s['model']}`　num_ctx：{s['num_ctx']}　"
        f"截图长边上限：{s['max_side']} px　每页每种调用 {args.n} 次　命令：`python tools/bench_vlm.py`", "",
        f"显存：{gpu}", "",
        "| 页面 | 编号元素数 | 描述调用 中位 / 最大 (ms) | 描述 输入 / 输出 token | 决策调用 中位 / 最大 (ms) | 决策 输入 / 输出 token |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['page']} | {r['elements']} | {r['desc_med']} / {r['desc_max']} | {r['desc_in']} / {r['desc_out']} "
                     f"| {r['dec_med']} / {r['dec_max']} | {r['dec_in']} / {r['dec_out']} |")
    lines += ["", "说明：第一次调用含模型加载时间，体现在\"最大\"列；N5 的参考目标是每步模型耗时中位数 ≤ 20 秒（不作为硬门槛）。", ""]
    out = ROOT / "docs" / "ver1" / "bench.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--driver", default=None)
    ap.add_argument("--model", default=None)
    sys.exit(asyncio.run(main(ap.parse_args())))
