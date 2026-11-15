# Bing_browse

本地视觉大模型做决策、确定性脚本负责执行的网页自动化测试系统。

**ver0**：访问网页的"手脚"层。Python 3.12 + 自写原生 CDP 驱动 Chrome（默认无头），决策由规则决策器完成。
**ver1**：接入本机视觉大模型（Ollama + qwen3.5:4b）做决策，与规则决策可切换。

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

### 决策方式：规则决策（默认）/ 视觉大模型（ver1）

视觉大模型在本机通过 Ollama 运行，截图和页面内容不离开这台电脑。每步先让模型描述页面构成（页面没变就复用），
再把带编号的标注图、元素清单、正文摘要和任务一起交给模型决策；模型只回答编号，点击位置取 DOM 里的精确位置。

```bash
ollama pull qwen3.5:4b                       # 默认模型（约 3.3 GB，8 GB 显存可全部放进显卡）
setx OLLAMA_CONTEXT_LENGTH 16384             # 上下文长度，设完重启 Ollama
python -m hands run cases/v01_shop_extract.yaml --decision vlm   # 命令行指定，优先于 config.yaml
python run_vlm_tests.py                      # ver1 一键验收 V01–V20（编号对应 spec 的 AC）
python tools/bench_vlm.py                    # 模型基准测试 → docs/ver1/bench.md
python tools/compare_deciders.py             # 规则决策 vs 大模型对比 → docs/ver1/compare.md
python tools/replay_step.py runs/<运行>/<用例>/step_003 -n 10   # 用存下的输入重放一步，看选号是否稳定
```

- `config.yaml` 的 `decision: rule | vlm` 设默认决策方式，`vlm:` 段可改模型、上下文长度、提示词目录等。
- 控制面板设置页"决策方式"可切到视觉大模型（任务运行中不能切换；模型服务不可用时显示提示）。
- **人眼比对**：面板顶部"人眼比对"链接 → 采集截图 → 逐页作答（选号题直接点图上的框）→ 让模型作答 → 查看报告，
  得到选号一致率、文字读取一致率、区块漏报与错报。比对集定义在 `compare/sets/`。
- **兜底拦截**：所有决策方式在点击前都会检查下单、支付类按钮和结算地址，命中就拒绝执行并记录；
  只能在 `config.yaml` 的 `guard:` 段增删清单，不能整体关闭。
- 用例可写 `hints`（线索词）和 `extract_fields`（要抽取的字段）；抽取的值会回查页面，查不到标"无依据"。

## 文档

- `docs/ver0/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`：手脚层
- `docs/panel/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`：控制面板（`python -m panel`）
- `docs/playwright/spec.md` → `plan.md` → `task.md` → `checklist.md` → `acceptance.md`：Playwright 驱动
- `docs/ver1/spec.md` → `plan.md` → `task.md` → `checklist.md`：视觉大模型决策；`bench.md`、`compare.md`、`prompt_log.md` 为实测记录
- 根目录 `*_v1.txt`：整体技术方案与调通方案
