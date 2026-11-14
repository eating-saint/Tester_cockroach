"""开发入口：python -m panel [--port 0] [--no-window]

--no-window：只起后台，打印面板地址，用普通 Chrome 打开即可（AC28）。
不加时打开 pywebview 窗口（见 app.py）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys


async def run_server(port: int):
    from .server import serve
    from .service import PanelService

    svc = PanelService()
    runner, actual, token = await serve(svc, port)
    print(f"面板地址: http://127.0.0.1:{actual}/", flush=True)
    print("（只接受本机访问；关闭：Ctrl+C）", flush=True)
    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        await svc.shutdown()
        await runner.cleanup()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="panel")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()
    if args.no_window:
        try:
            asyncio.run(run_server(args.port))
        except KeyboardInterrupt:
            pass
    else:
        from .app import main as app_main
        app_main(port=args.port)


if __name__ == "__main__":
    main()
