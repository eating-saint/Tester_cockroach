# 交付基线

ver0（无头浏览器"手脚"层，原生 CDP 驱动 Chrome）M1–M10、T01–T17、T20 已于 2026-10-29 验收：
checklist 37/37 通过，`pytest -q` 37 passed，`python run_tests.py` 17/17、T17 共 550/550，
Bing 用例（t20_bing.yaml）有界面和无头各跑一次通过（等待逻辑修复后的回归）。
不在 ver0 范围内：大模型决策、视觉坐标、鼠标轨迹模块与 Overlay 高亮、T18/T19。

