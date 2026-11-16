"""夹具 HTTP 服务：静态文件 + 延迟接口 + 404。同时监听 127.0.0.1 与 ::1（localhost 用于制造跨源）。

python fixtures/server.py [port]
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/delay":
            time.sleep(int(q.get("ms", ["0"])[0]) / 1000)
            return self._json({"data": q.get("data", [""])[0]})
        if u.path == "/api/page":
            n = int(q.get("n", ["1"])[0])
            return self._json({"items": [f"第{'一二三四五'[n - 1] if n <= 5 else n}页-{i}" for i in range(1, 11)]})
        if u.path == "/api/slowpage":           # 桌面驱动夹具：服务端慢响应的整页（导航本身慢）
            time.sleep(int(q.get("ms", ["3000"])[0]) / 1000)
            body = ("<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>慢页面</title></head>"
                    "<body style='font:16px sans-serif'><h1 id='done'>慢页面已加载</h1>"
                    "<button>慢页面按钮</button></body></html>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if u.path == "/missing":
            return self.send_error(404, "missing")
        return super().do_GET()

    def guess_type(self, path):
        t = super().guess_type(path)
        if t.startswith("text/") and "charset" not in t:
            t += "; charset=utf-8"
        return t


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # 浏览器导航离开时会中止慢请求，属于预期情况
        pass


class V6Server(QuietServer):
    address_family = socket.AF_INET6


class Handle:
    def __init__(self, servers):
        self.servers = servers
        self.threads = []
        for s in servers:
            th = threading.Thread(target=s.serve_forever, daemon=True)
            th.start()
            self.threads.append(th)

    def stop(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()


def start(port: int = 8765) -> Handle:
    h = partial(Handler, directory=str(ROOT))
    servers = [QuietServer(("127.0.0.1", port), h)]
    try:
        servers.append(V6Server(("::1", port), h))
    except OSError as e:
        print(f"[fixtures] IPv6 ::1 listen failed ({e}); localhost may not resolve", file=sys.stderr)
    for s in servers:
        s.daemon_threads = True
    return Handle(servers)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    start(port)
    print(f"fixtures serving on http://127.0.0.1:{port} and http://localhost:{port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
