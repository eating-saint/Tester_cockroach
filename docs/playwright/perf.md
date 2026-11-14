# 两种驱动性能对比

生成时间：2026-11-14 01:39　机器：Intel64 Family 6 Model 198 Stepping 2, GenuineIntel，Windows-11-10.0.26200-SP0　Chrome：C:\Program Files\Google\Chrome\Application\chrome.exe　Playwright：1.63.0
每项读取 / 点击重复 20 次取中位数。命令：`python tools/bench_drivers.py`

| 项目 | CDP | Playwright |
|---|---|---|
| 浏览器启动（start + new_page，10 次中位数） | 1046.5 ms | 2029.3 ms |
| 读取页面 F1（build_page_state 中位数） | 4.0 ms | 23.0 ms |
| 读取页面 F10（build_page_state 中位数） | 3.8 ms | 17.4 ms |
| F1 元素数 | 16  | 16  |
| F10 元素数 | 6  | 6  |
| 一次点击（含等待网络安静） | 524.9 ms | 579.6 ms |
| 一次点击（不含等待） | 10.0 ms | 65.5 ms |
| 推流帧率（panel_bench 动画页，10 秒） | 99.9 帧/秒 | 100.0 帧/秒 |
| T17 50 轮（总耗时；通过率与内存） | 1198s；50 轮通过率 550/550；Python 内存 55→55MB，Chrome 内存 580→568MB（第5→50轮） | 1253s；50 轮通过率 550/550；Python 内存 59→75MB，Chrome 内存 611→686MB（第5→50轮）；内存增长超过 20% |
