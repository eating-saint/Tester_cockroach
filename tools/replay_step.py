"""单步重放（ver1 N3、AC15）：用记录里存下的完整请求重新问模型 n 次，统计"工具 + 编号"与原回答一致的比例。

用法：python tools/replay_step.py <step_dir> [<step_dir> ...] [-n 10] [--model qwen3.5:4b]
step_dir 是运行记录里的 step_xxx 目录（里面有 vlm/request.json、vlm/response.json、vlm/annotated.png）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hands.config import load_config, vlm_settings  # noqa: E402
from hands.vlm.client import OllamaClient  # noqa: E402
from hands.vlm.decider import _as_no, _parse_json  # noqa: E402


def choice(text: str):
    """回答里的 (工具, 编号)；编号没有就是 None；解析不了返回 None。"""
    try:
        obj = _parse_json(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    args = obj.get("arguments") or {}
    return (obj.get("tool"), _as_no(args.get("element")) if isinstance(args, dict) else None)


async def replay(step_dir: Path, n: int, client) -> dict:
    v = step_dir / "vlm"
    req = json.loads((v / "request.json").read_text(encoding="utf-8"))
    orig = choice(json.loads((v / "response.json").read_text(encoding="utf-8"))["raw"])
    imgs = [(v / name).read_bytes() for name in req.get("images", [])]
    got = []
    for _ in range(n):
        r = await client.chat(req["system"], req["user"], imgs, req.get("schema"))
        got.append(choice(r.text))
    same = sum(1 for g in got if g == orig)
    return {"step": str(step_dir), "original": orig, "same": same, "n": n, "answers": got}


async def main(args) -> int:
    s = vlm_settings(load_config())
    if args.model:
        s["model"] = args.model
    client = OllamaClient(s)
    await client.check()
    total_same = total = 0
    rows = []
    for d in args.steps:
        r = await replay(Path(d), args.n, client)
        rows.append(r)
        total_same += r["same"]
        total += r["n"]
        print(f"{r['step']}  原回答 {r['original']}  一致 {r['same']}/{r['n']}", flush=True)
    rate = total_same / total if total else 0.0
    print(f"总体选号一致率：{total_same}/{total} = {rate:.0%}")
    if args.json:
        Path(args.json).write_text(json.dumps({"rate": rate, "rows": rows}, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="+")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--model", default=None)
    ap.add_argument("--json", default=None, help="把结果另存为 JSON")
    sys.exit(asyncio.run(main(ap.parse_args())))
