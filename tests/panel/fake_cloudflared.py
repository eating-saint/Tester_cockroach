"""cloudflared 的替身：打印一行假隧道地址后常驻（测试用，不联网）。

python fake_cloudflared.py [--silent] [--exit-after N] tunnel --no-autoupdate --url http://127.0.0.1:8791
  --silent       不打印地址（测"拿不到地址"）
  --exit-after N N 秒后自己退出（测"隧道意外断开"）
"""
import sys
import time

args = sys.argv[1:]
silent = "--silent" in args
exit_after = None
if "--exit-after" in args:
    exit_after = float(args[args.index("--exit-after") + 1])
print("2026-11-16T00:00:00Z INF Thank you for trying Cloudflare Tunnel.", flush=True)
if not silent:
    print("2026-11-16T00:00:01Z INF |  https://fake-test.trycloudflare.com  |", flush=True)
t0 = time.time()
while exit_after is None or time.time() - t0 < exit_after:
    time.sleep(0.1)
sys.exit(3)
