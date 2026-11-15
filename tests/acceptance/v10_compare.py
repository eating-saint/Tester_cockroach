"""V10（AC10、AC17，F10、N5）：同一批用例分别用两种决策跑完，对比表的成功率、平均步数、分段耗时、显存峰值都有数值。"""
import re
import sys
import types

from hands.config import PROJECT_ROOT

NEEDS_MODEL = True


async def run(ctx):
    await ctx.close_browser()                      # 对比工具自己开浏览器（同一个用户目录）
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import compare_deciders as cd
    args = types.SimpleNamespace(pairs="v01r_shop_rule:v01_shop_extract", repeat=1, driver=ctx.config.driver,
                                 model=None, append=False)
    out = await cd.compare(args)
    text = out.read_text(encoding="utf-8")
    need = ["任务成功率", "平均步数", "每步决策耗时中位数", "每步模型耗时中位数", "每步动作与等待中位数", "显存峰值"]
    rows = {}
    for line in text.splitlines():
        for k in need:
            if line.startswith(f"| {k}"):
                rows[k] = [c.strip() for c in line.strip("|").split("|")]
    miss = [k for k in need if k not in rows]
    if miss:
        return False, f"对比表缺少 {miss}"
    vlm_vals = {k: rows[k][-1] for k in need}
    empty = [k for k, v in vlm_vals.items() if v in ("-", "")]
    if empty:
        return False, f"大模型一列没有数值：{empty}"
    m = re.search(r"(\d+) ms", vlm_vals["每步模型耗时中位数"])
    med = int(m.group(1)) if m else None
    return True, (f"对比表已生成 {out}：大模型成功率 {vlm_vals['任务成功率']}，每步模型耗时中位数 {med} ms"
                  f"（N5 参考 ≤ 20000 ms，{'达到' if med is not None and med <= 20000 else '未达到'}，只记录），"
                  f"显存峰值 规则 {rows['显存峰值'][1]} / 大模型 {rows['显存峰值'][2]}")
