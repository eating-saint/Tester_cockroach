# Playwright 驱动方案 Checklist

> 每一项通过运行代码或观察行为来验证。证据（命令输出摘要、runs/ 目录、截图）记入 `docs/playwright/acceptance.md`。
> 标 [人工] 的需要用户在场；其余 Claude 可自行运行。浏览器只用 `profiles/ver0`，各项不能并行。

## 一、向后兼容（AC1、N1）
- [x] C1 默认配置下单元测试全过（验证：`pytest -q`，通过数 ≥ 改动前 77，新增测试全部通过）
- [x] C2 默认配置下一键验收全过（验证：`python run_tests.py --rounds 3`，T01–T17、T21、T22 全部通过；结果目录名不带驱动后缀）
- [x] C3 默认配置下面板验收全过（验证：`python run_panel_tests.py`，自动项与此前 26 项一致全部通过，新增 P31 通过）
- [x] C4 未安装 Playwright 时核心依赖不变（验证：`pip show` 对比 `pyproject.toml` 的 `dependencies` 与改动前相同；`playwright` 只出现在可选依赖 `pw` 中）
- [x] C5 未安装 Playwright 时选它给出明确提示（验证：在只装 `pip install -e .` 的临时虚拟环境中运行 `python -m hands run cases/t16_report.yaml --driver playwright`，看到含 `pip install -e .[pw]` 的中文提示，退出码非零；同环境默认驱动跑该用例正常通过）
- [x] C6 未安装 Playwright 时导入 hands 不报错（验证：同一临时环境 `python -c "import hands.runner, hands.driver"` 无异常）

## 二、驱动选择（F1）
- [x] C7 配置文件可选驱动（验证：`config.yaml` 改为 `driver: playwright` 后不带参数运行 `python -m hands run cases/t16_report.yaml`，报告 / 日志显示使用 Playwright 驱动；改回 `cdp` 后显示 CDP）
- [x] C8 命令行优先于配置文件（验证：`config.yaml` 为 `cdp` 时加 `--driver playwright` 运行，实际使用 Playwright）
- [x] C9 非法取值被拒绝（验证：`--driver foo` 时命令行报参数错误；`config.yaml` 写 `driver: foo` 时启动报明确错误）

## 三、Playwright 驱动功能（AC2、F2–F7）
- [x] C10 一键验收在 Playwright 驱动下全过（验证：`python run_tests.py --driver playwright`，T01–T17、T21、T22 全部通过；若有滚轮相关不通过项，acceptance.md 写明原因且用户确认列为已知差异）
- [x] C11 使用本机 Chrome 与同一用户目录（验证：Playwright 驱动运行中查看 Chrome 进程命令行，可执行文件为 `config.chrome_path`、`--user-data-dir` 指向 `profiles/ver0`；未执行 `playwright install`，`%LOCALAPPDATA%\ms-playwright` 下无新下载内核）
- [x] C12 不去掉自动化标记（验证：Playwright 驱动下 `page.evaluate("navigator.webdriver")` 为 `true`）
- [x] C13 有界面 / 无界面都能运行（验证：`python -m hands run cases/t16_report.yaml --driver playwright` 与加 `--headed` 各跑一次都通过，有界面时可见 Chrome 窗口）
- [x] C14 跨源 iframe 元素读取与点击（验证：T08 在 Playwright 驱动下通过，日志中两个 iframe 按钮误差 ≤ 1 px、点击结果正确）
- [x] C15 等待与超时行为（验证：T06、T07 在 Playwright 驱动下通过；T07 的结果中列出慢请求地址）
- [x] C16 崩溃重跑（验证：T02 在 Playwright 驱动下通过，检测耗时 < 5 秒，报告 crashes=1）
- [x] C17 新标签页与后退（验证：T12 在 Playwright 驱动下通过）
- [x] C18 鼠标轨迹与干扰项（验证：T21 在 Playwright 驱动下通过，含跨源 iframe 命中、同种子轨迹一致）
- [x] C19 运行产物齐全（验证：Playwright 驱动跑完一个用例后，runs/ 目录中每步都有 pagestate、decision、action、截图，report.html 可打开且元素框标注位置与截图吻合——人工抽看一步）

## 四、封闭 Shadow DOM（AC3、F3）
- [x] C20 封闭 Shadow DOM 按钮能看到、能点中（验证：`python run_tests.py --driver playwright --only T22` 通过；`python run_tests.py --only T22`（CDP）也通过）

## 五、验证码转人工（AC4、F7）
- [x] C21 [人工] 命令行转人工后能继续（验证：`python -m hands run cases/extra_captcha.yaml --driver playwright`：看到转人工提示，Chrome 切到有界面，人工回车后用例继续，report.html 判定通过且记录人工介入起止时间）

## 六、面板（AC5、F8、F9）
- [x] C22 面板验收在 Playwright 驱动下通过（验证：`python run_panel_tests.py --driver playwright`，除 P30 标"不适用"外自动项全部通过）
- [x] C23 切换控件可见且生效（验证：`python -m panel --no-window --port 8790`，浏览器打开面板，在驱动下拉选 Playwright：状态栏显示"驱动：Playwright"，浏览器状态回到运行中，预览有画面）
- [x] C24 有任务时不能切换（验证：P31 通过——任务运行中 `POST /api/browser/driver` 返回 409、下拉为禁用；空闲时切换后状态中的 driver 变为新值）
- [x] C25 Playwright 驱动下预览与手动输入（验证：P05、P14 在 Playwright 驱动下通过——推流有帧、预览上点击和输入"你好abc"转发成功）
- [x] C26 Playwright 驱动下真实网站采集被明确拒绝（验证：Playwright 驱动时在面板"采集"标签点真实网站采集，看到"Playwright 驱动不支持真实网站采集"提示；本地采集页正常打开）
- [x] C27 驱动可保存为默认（验证：面板设置里保存为默认后，`config.yaml` 的 `driver` 被改写且注释保留；重启面板后默认即为该驱动）

## 七、真实网站（AC6、F4）
- [x] C28 [人工] Google 用例跑通（验证：`python -m hands run cases/g02_fanzhendong.yaml --driver playwright --headed` 判定通过；遇 reCAPTCHA 由用户人工处理；截图核对确实点进了目标页面并读到内容，不只看自动判定）

## 八、性能对比（AC7、F10）
- [x] C29 对比表数值齐全（验证：`python tools/bench_drivers.py` 运行结束，`docs/playwright/perf.md` 中启动耗时、读取页面耗时（F1、F10）、点击耗时、推流帧率、T17 50 轮总耗时与内存变化，CDP 与 Playwright 两列都有数值，并注明机器与 Chrome 版本）

## 九、代码约束（N2–N4）
- [x] C30 上层不感知驱动（验证：`rg -n "session.send" src/hands/actions.py src/hands/pagestate.py src/hands/screenshot.py src/hands/humanize.py src/hands/runner.py src/hands/captcha.py` 无结果；`rg -n "driver ==|driver !=" src/hands` 只出现在 `driver.py`、`dom.py` 的转调一行）
- [x] C31 hands 不依赖 panel（验证：`rg -n "import panel|from panel" src/hands` 无结果）
- [x] C32 无固定 sleep（验证：T06 的源码扫描覆盖 `src/hands/pw/` 且通过）
- [x] C33 坐标为 CSS 像素（验证：C14、T05 在 Playwright 驱动下误差 ≤ 1 px 即证明）

## 端到端场景
- [x] E1 命令行一次完整切换：默认 CDP 跑 `t16_report.yaml` 通过 → `--driver playwright` 跑同一用例通过 → 两份 report.html 判定一致、步数一致（验证：对比两份 report.json 的 passed 与 steps）
- [x] E2 面板一次完整切换：面板启动（CDP）→ 跑 `t13_reddit_yes` 通过 → 切到 Playwright → 再跑同一用例通过 → 切回 CDP 再跑一次通过；历史页三条记录都在、报告都能打开（验证：面板界面与 `runs/` 目录）
