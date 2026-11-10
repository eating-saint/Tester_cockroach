"""T17 稳定性：T03–T13 连续跑 N 轮（默认 50），通过率 100%，内存不持续上涨（第 N 轮较第 5 轮增长 ≤ 20%）。"""
import gc
import importlib

from common import chrome_ws_mb, python_rss_mb

MODULES = ["t03_counts", "t04_visibility", "t05_boxes", "t06_dynamic", "t07_timeout", "t08_frames",
           "t09_form", "t10_lazy", "t11_fault", "t12_nav", "t13_reddit"]


async def run(ctx):
    mods = [importlib.import_module(m) for m in MODULES]
    rounds = ctx.rounds
    total = passed = 0
    failures = []
    mem = {}
    probe = min(5, rounds)
    for i in range(1, rounds + 1):
        for m in mods:
            total += 1
            try:
                ok, why = await m.run(ctx)
            except Exception as e:  # 记录并继续
                ok, why = False, f"{type(e).__name__}: {e}"
            if ok:
                passed += 1
            else:
                failures.append(f"第{i}轮 {m.__name__}: {why}")
        if i in (probe, rounds):
            gc.collect()
            mem[i] = (python_rss_mb(), chrome_ws_mb(ctx.config.profile_dir))
        print(f"    [T17] 第 {i}/{rounds} 轮完成，累计通过 {passed}/{total}", flush=True)
    py0, ch0 = mem[probe]
    py1, ch1 = mem[rounds]
    msg = (f"{rounds} 轮通过率 {passed}/{total}；Python 内存 {py0:.0f}→{py1:.0f}MB，"
           f"Chrome 内存 {ch0:.0f}→{ch1:.0f}MB（第{probe}→{rounds}轮）")
    if failures:
        return False, msg + "；失败: " + "; ".join(failures[:3])
    if rounds > probe and (py1 > py0 * 1.2 or ch1 > ch0 * 1.2):
        return False, msg + "；内存增长超过 20%"
    return True, msg
