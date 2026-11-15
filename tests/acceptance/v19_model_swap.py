"""V19（AC19，N7）：改提示词文件后不用改代码，新内容出现在模型请求里；
只改配置换成 qwen3.5:2b，仿电商任务能运行到结束，结果追加进对比表。"""
import shutil
import sys
import tempfile
import types
from pathlib import Path

from hands.config import PROJECT_ROOT
from hands.vlm.prompts import builtin_dir

from vcommon import jload, model_ok, run_case, steps

NEEDS_MODEL = True
OTHER = "qwen3.5:2b"
MARK = "【V19 提示词标记 7f3a】"


async def run(ctx):
    notes = []
    # ① 提示词目录指向副本，副本里加一句标记 → 请求里出现标记
    tmp = Path(tempfile.mkdtemp(prefix="v19_prompts_"))
    shutil.copytree(builtin_dir(), tmp, dirs_exist_ok=True)
    p = tmp / "decide.md"
    p.write_text(p.read_text(encoding="utf-8") + f"\n{MARK}\n", encoding="utf-8")
    res, rec, cdir = await run_case(ctx, "v03_inject.yaml", vlm={"prompts_dir": str(tmp)}, label="v19-prompts")
    users = [jload(s / "vlm" / "request.json")["user"] for s in steps(cdir) if (s / "vlm" / "request.json").exists()]
    if not users or not all(MARK in u for u in users):
        return False, "改过的提示词没有出现在模型请求里"
    notes.append(f"提示词副本里的标记出现在 {len(users)} 次请求里（未改代码）")
    # ② 只改配置换模型
    ok, info = await model_ok(ctx, OTHER)
    if not ok:
        return False, "；".join(notes + [f"换模型这一项未做：{info}"])
    await ctx.close_browser()
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import compare_deciders as cd
    args = types.SimpleNamespace(pairs="v01r_shop_rule:v01_shop_extract", repeat=1, driver=ctx.config.driver,
                                 model=OTHER, append=True)
    out = await cd.compare(args)
    last = out.read_text(encoding="utf-8").split("## ")[-1]
    if OTHER not in last or "| vlm | v01_shop_extract |" not in last:
        return False, "；".join(notes + ["对比表没有追加换模型后的结果"])
    line = next(x for x in last.splitlines() if x.startswith("| vlm | v01_shop_extract |"))
    notes.append(f"换成 {OTHER} 跑到结束并追加进对比表：{line}")
    return True, "；".join(notes)
