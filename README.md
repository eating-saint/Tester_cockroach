# Bing_browse

本地视觉大模型做决策、确定性脚本负责执行的网页自动化测试系统。

当前版本 **ver0**：访问网页的"手脚"层。Python 3.12 + 自写原生 CDP 驱动 Chrome（默认无头），大模型的位置暂由规则决策器代替。

## 运行

```bash
pip install -e .[test]
python -m pytest                              # 单元测试
python run_tests.py                           # 一键验收 T01–T17
python -m hands run cases/t20_bing.yaml --headed   # Bing 冒烟，有界面
```

### 驱动：CDP（默认）/ Playwright

两种驱动功能一致，可随时切换。Playwright 是可选依赖，用本机 Chrome 和同一用户目录，不下载内核。

```bash
pip install -e .[pw]                                            # 安装 Playwright（可选）
python -m hands run cases/t16_report.yaml --driver playwright   # 命令行指定，优先于 config.yaml
python run_tests.py --driver playwright                         # 一键验收（结果目录带 -playwright 后缀）
python run_panel_tests.py --driver playwright                   # 面板验收
```

- `config.yaml` 的 `driver: cdp | playwright` 设默认驱动。
- 控制面板顶部"驱动"下拉框可切换（有任务运行时不能切换），"保存为默认"会写回 `config.yaml`。
- 差异与性能对比见 `docs/playwright/acceptance.md`、`docs/playwright/perf.md`。

## 文档

- `docs/ver0/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`：手脚层
- `docs/panel/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`：控制面板（`python -m panel`）
- `docs/playwright/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`：Playwright 驱动
- 根目录 `*_v1.txt`：整体技术方案与调通方案
