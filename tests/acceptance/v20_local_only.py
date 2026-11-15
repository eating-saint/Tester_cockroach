"""V20（AC20，N1、N8）：用本机局域网 IP 访问不到模型服务；两次运行、两次比对结果各有独立目录、互不覆盖。"""
import json
import socket
import tempfile
from pathlib import Path

import httpx

from hands.vlm import compare as core

from vcommon import run_case

NEEDS_MODEL = False


def lan_ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                                           # 不发包：只是让系统选出出网用的本机地址
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


async def run(ctx):
    notes = []
    ips = lan_ips()
    if not ips:
        return False, "找不到本机局域网 IP"
    reachable = []
    for ip in ips:
        try:
            r = httpx.get(f"http://{ip}:11434/api/tags", timeout=3)
            reachable.append(f"{ip} → {r.status_code}")
        except httpx.HTTPError:
            pass
    if reachable:
        return False, f"模型服务能从局域网地址访问：{reachable}（请检查 OLLAMA_HOST 是否改成了 0.0.0.0）"
    notes.append(f"局域网地址 {ips} 访问 :11434 均连接失败")
    # 两次运行目录不同
    r1 = await run_case(ctx, "v03_inject.yaml", decision="rule", label="v20")
    r2 = await run_case(ctx, "v03_inject.yaml", decision="rule", label="v20")
    if r1[1].root == r2[1].root or not r1[1].root.exists() or not r2[1].root.exists():
        return False, "两次运行目录相同或缺失"
    notes.append(f"两次运行目录 {r1[1].root.name}、{r2[1].root.name}")
    # 两次比对结果目录不同
    sd = Path(tempfile.mkdtemp(prefix="v20_cmp_"))
    pd = sd / "pages" / "p1"
    pd.mkdir(parents=True)
    els = [{"no": 1, "role": "searchbox", "label": "搜索商品"}, {"no": 2, "role": "link", "label": "Spicy Ramen"}]
    (pd / "questions.json").write_text(json.dumps(core.make_questions(els, 1), ensure_ascii=False), encoding="utf-8")
    (pd / "human.json").write_text(json.dumps({"answers": {"search": 1}}), encoding="utf-8")
    (pd / "model").mkdir()
    (pd / "model" / "m.json").write_text(json.dumps({"answers": {"search": 1}}), encoding="utf-8")
    a, b = core.report(sd, "m"), core.report(sd, "m")
    if a == b or not (a / "report.json").exists() or not (b / "report.json").exists():
        return False, "两次比对结果目录相同或缺失"
    notes.append(f"两次比对结果目录 {a.name}、{b.name}")
    return True, "；".join(notes)
