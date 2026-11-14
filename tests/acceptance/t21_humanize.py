"""T21 鼠标轨迹与干扰项（F12、F5）。

检查：
1. 从中心按钮出发点 8 个方向的按钮，再点 12×12 的小按钮：页面记录的点击都落在正确按钮内，
   每次点击前页面收到至少 5 个 mousemove（轨迹确实经过了中间点，不是瞬移），8 个方向全部覆盖。
2. 滚轮乱动每次都发生，并回滚到原位，误差不超过 1 像素。
3. 只开轨迹时，同一种子跑两遍，轨迹逐点相同；换一个种子，轨迹不同。
4. 跨源 iframe 里的按钮在轨迹模式下也能点中（坐标偏移正确）。
5. 生成轨迹回放图，存到产物目录供人工查看。
"""
from hands.actions import ActionExecutor
from hands.humanize import HumanizeConfig, Humanizer
from hands.screenshot import draw_trajectory

from common import load_expected, read_elements

CFG = HumanizeConfig.from_dict({
    "mouse": True,
    "think": {"enabled": True, "prob": 1, "min_ms": 30, "max_ms": 80},
    "scroll_jitter": {"enabled": True, "prob": 1, "min_px": 80, "max_px": 300, "max_ticks": 2,
                      "pause_min_ms": 30, "pause_max_ms": 80},
})
MOUSE_ONLY = HumanizeConfig.from_dict({"mouse": True})


async def _find(page, name):
    els, _ = await read_elements(page)
    return next((e for e in els if e.name == name), None)


async def one_pass(ctx, seed: int, save_png: bool, cfg: HumanizeConfig = CFG):
    exp = load_expected("f12_mouse")
    page = await ctx.page()
    await page.navigate(ctx.url("f12_mouse/index.html"))
    ax = ActionExecutor(page, Humanizer(cfg, seed), capture_trajectory=save_png)
    order = []
    for name in exp["around"]:
        order += [exp["center"], name]
    order.append(exp["tiny"])
    trajs, problems, residuals = [], [], []
    for i, name in enumerate(order):
        e = await _find(page, name)
        if e is None:
            return None, [f"未读到按钮 {name}"], []
        r = await ax.click(e)
        if not r.ok or r.trajectory is None:
            problems.append(f"点击 {name} 失败: {r.error}")
            continue
        trajs.append(r.trajectory)
        for d in r.disturbances:
            if d["kind"] == "scroll_jitter":
                residuals.append(abs(d["residual_px"]))
        if save_png and r.trajectory_png and i in (1, 5, 9, 16):
            ctx.artifact(f"t21/traj_{i:02d}_{name}.png").write_bytes(
                draw_trajectory(r.trajectory_png, r.trajectory, 1.0))
    clicks = await page.evaluate("JSON.stringify(window.__clicks)")
    import json
    clicks = json.loads(clicks)
    ids = {"中心": "b-c", "东": "b-e", "东南": "b-se", "南": "b-s", "西南": "b-sw", "西": "b-w",
           "西北": "b-nw", "北": "b-n", "东北": "b-ne", "小按钮": "b-tiny"}
    if len(clicks) != len(order):
        problems.append(f"页面记录到 {len(clicks)} 次点击，应为 {len(order)}")
    for name, c in zip(order, clicks):
        x, y, (rx, ry, rw, rh) = c["x"], c["y"], c["rect"]
        if c["id"] != ids[name]:
            problems.append(f"想点 {name}，页面记录点中 {c['id']}")
        if not (rx <= x <= rx + rw and ry <= y <= ry + rh):
            problems.append(f"{name} 点击坐标 ({x},{y}) 不在按钮框 {c['rect']} 内")
        if c["moves"] < exp["min_moves_per_click"]:
            problems.append(f"{name} 前只收到 {c['moves']} 个 mousemove")
    if any(r > exp["max_residual_px"] for r in residuals):
        problems.append(f"滚轮回滚误差 {max(residuals)}px")
    if cfg.scroll_jitter.enabled and len(residuals) != len(order):
        problems.append(f"滚轮乱动发生 {len(residuals)} 次，应为 {len(order)}（prob=1）")
    return trajs, problems, residuals


async def run(ctx):
    t1, p1, res = await one_pass(ctx, 1234, save_png=True)
    if p1:
        return False, "; ".join(p1[:4])
    dirs = {t["direction"] for t in t1[1:16:2]}          # 从中心出发去 8 个方向的那 8 次
    if len(dirs) != 8:
        return False, f"中心到四周只覆盖了方向 {sorted(dirs)}"
    ta, pa, _ = await one_pass(ctx, 1234, save_png=False, cfg=MOUSE_ONLY)
    tb, pb, _ = await one_pass(ctx, 1234, save_png=False, cfg=MOUSE_ONLY)
    if pa or pb or [t["points"] for t in ta] != [t["points"] for t in tb]:
        return False, "同一种子两次运行的轨迹不一致" + (f": {(pa or pb)[:2]}" if pa or pb else "")
    tc, pc, _ = await one_pass(ctx, 999, save_png=False, cfg=MOUSE_ONLY)
    if pc:
        return False, "; ".join(pc[:4])
    if [t["points"] for t in tc] == [t["points"] for t in ta]:
        return False, "换了种子轨迹却完全相同"
    # 跨源 iframe 里的按钮
    page = await ctx.page()
    await page.navigate(ctx.url("f05_iframe/index.html"))
    await page.refresh_offsets()
    ax = ActionExecutor(page, Humanizer(CFG, 77))
    for b in load_expected("f05_iframe")["buttons"]:
        e = await _find(page, b["name"])
        r = await ax.click(e)
        got = await page.evaluate("document.getElementById('result').textContent")
        if not r.ok or got != b["result"]:
            return False, f"轨迹模式点击 {b['name']} 结果 {got!r}（{r.error}）"
    over = sum(1 for t in t1 if t["overshoot_point"])
    tpls = len({t["template"] for t in t1})
    avg_pts = sum(t["n_points"] for t in t1) / len(t1)
    return True, (f"17 次点击全部命中（含 12px 小按钮），8 个方向全覆盖，{tpls} 种模板，{over} 次过冲，"
                  f"平均每次 {avg_pts:.0f} 个轨迹点；滚轮乱动 {len(res)} 次，最大回滚误差 {max(res):.1f}px；"
                  f"同种子轨迹一致、换种子不同；跨源 iframe 命中")
