# Playwright 驱动方案 验收记录

对应 `docs/playwright/checklist.md`（C1–C33、E1–E2）。证据目录均在 `runs/` 下。

## 开发中的偏离与发现

| 项 | 计划 | 实际 | 原因 |
|---|---|---|---|
| `PwPage.evaluate` | task W9 第 8 步：用间接 eval 求值 | 直接 `page.evaluate(表达式字符串)` | 实测 Playwright 对 `"document.title"`、`"(()=>{…})()"` 这类字符串直接按表达式求值，结果正确；间接 eval 在禁止 `unsafe-eval` 的 CSP 页面上会被拦 |
| 长时间运行内存 | plan 未涉及 | 新增页面方法 `fresh_start()`：runner 在每个用例开始时调用；CDP 驱动为空操作；Playwright 驱动在累计请求 ≥ 500 时换新标签页、关掉旧的 | 全量验收 T17 首次失败：550/550 通过但 Python 内存 60→75 MB（+25%，标准 ≤ 20%）。排查发现 Playwright 给每个 Request / Response / Frame 在 Python 端登记对象，标签页关闭前不释放（每类上限 1 万个）；实测换标签页后对象数归零、内存持平 |
| iframe 坐标 | plan：点击定位直接用 `locator.bounding_box()`；元素坐标用“bounding_box − frame 内坐标”得偏移 | 都改为逐层累加 `<iframe>` 的 `getBoundingClientRect` + 边框 + 内边距 | 第一次全量验收 T08 失败：Playwright 的 `bounding_box()` 对**跨进程** iframe 内元素少算了 iframe 的边框和内边距（F5 夹具上差 12 px；同源 iframe 正确） |
| 新动作 `open_tab` + 规则条件 `not_visited`（用户 11-14 批准） | spec 未涉及；原需求只有点击 / 输入 / 按键 / 滚动 / 后退（`ver0_无头版…txt:25`、`docs/ver0/spec.md:28`） | `actions.open_tab(el, switch)`：Ctrl+点击在新标签页打开，原页不关，`switch` 决定是否切过去；`rule_decider`：元素条件 `not_visited: true`，按 href（无 href 时按 角色:名字）排除本用例已点过的；`decision.py` 登记工具；`runner._execute` 执行 | 用户演示需求：Google 前三页结果逐个点开、每页搜索结果留一个标签页。单元测试 `tests/unit/test_open_tab_visited.py`；两种驱动用临时用户目录实测通过；CDP 受影响项 T09/T12/T13/T14/T16 通过 |
| 新标签页识别 | — | 两个驱动都新增 `_spawned` 队列：没有打开者关系的新标签页单独记下，`open_tab` 从这里取 | Ctrl+点击打开的标签页没有 opener，原逻辑（只认 opener 是当前页的）认不出 |
| 等待：结束事件收不到的脚本请求 | — | `Monitor.req_started`：**Script 类**请求与 worker 类（type 为空 / Other）一样，从发出起计时，挂满 `LONG_POLL_S`（3 秒）不再阻塞“网络安静”（两驱动共用记账层）；页面自己的 fetch / XHR 照旧阻塞到超时 | g07 实跑：Google 的 `recaptcha/enterprise/webworker.js` 结束事件发到 worker 会话、页面会话收不到，原规则不覆盖，每步白等 30 秒。改了三版：① 到 load 时放行所有非文档请求 → 全量 T07（F4 慢 fetch 应超时）失败；② 到 load 时只放行 Script → 命令行实跑点 Google 卡片（同文档改网址）后生命周期不再到 load，仍白等 30 秒；③ 现行：Script 从发出起计时。真正拖住页面的脚本会推迟 load，等待逻辑先等 load，不受影响。单元测试 `test_pre_load_script_released_after_long_poll`、`test_pre_load_fetch_keeps_blocking`；T06 / T07 / T10 / T12 两驱动通过 |
| 验证码检测：不可见 iframe（ver0 起就有，与驱动无关） | — | `captcha.detect` 的 iframe 命中只看可见且面积 > 0 的 iframe（网址、正文命中不变） | C28 实跑 g02：点进新浪文章页后，广告带进来的两个 0×0 不可见 `recaptcha/api2/aframe` 命中 `captcha` 标记，误报转人工，命令行空等 15 分钟。真正要人处理的验证框（reCAPTCHA 勾选框 / 图片题、Cloudflare 小组件）都可见且有尺寸。单元测试 `test_hidden_zero_size_iframe_not_hit` |
| Playwright 沙箱 | — | `launch_persistent_context(..., chromium_sandbox=True)` | Playwright 默认加 `--no-sandbox`（Chrome 顶部提示“不受支持的命令行标记”），CDP 驱动不关沙箱；改后命令行里不再有该参数 |
| 用例 g07 | — | `cases/g07_unique_toys.yaml`：结果链接补 `/goto?` 形式；步数上限 100 | Google 现在的结果链接是 `https://www.google.com/goto?url=…` |
| `open_tab` 切过去时切到前台 | — | `open_tab(switch=True)` 接管新标签页后调用 `page.bring_to_front()` | 命令行有界面跑 g07（CDP）：Ctrl+点击打开的是后台标签页，Chrome 不绘制后台标签页，下一步截图 `Page.captureScreenshot` 一直等不到、30 秒超时。三次都卡在 `open_tab` 后一步；修后跑过了这一步。面板里没暴露是因为面板推流 / 前置窗口 |
| 用例时限不含等人时间 | ver0 F12：“人工按回车后重新加载页面继续”（`docs/ver0/spec.md:33`） | `runner._attempt`：每次人工介入结束后，用例截止时间顺延本次等人的时长 | C21 首次实跑：用户处理假验证码用了两分多钟，超过 `extra_captcha` 的 120 秒时限，验证码一消失即判 `timeout`。ver0 起就有，与驱动无关（P15 人工介入是瞬间完成的，没暴露）。验证：时限 2 秒、模拟人工 3.5 秒，两驱动都判通过；C21 重跑通过 |
| Playwright 点击取点 | plan：点击定位与 CDP 驱动一致 | `PwPage.locate_box` 改为取 `getClientRects()` 第一个非空矩形（无则退回 `getBoundingClientRect`），与 CDP 驱动 `DOM.getContentQuads` 第一个矩形一致 | C28 首次实跑：Google 结果链接是“网址一行 + 标题一行”的行内 `<a>`，Playwright 驱动按两行外接矩形中心点击，落在网址右边的空白处（不属于链接），连点 32 次无反应、步数用尽。这是 Playwright 驱动没做到 1:1。新增夹具 F16、验收 T23（外接矩形中心不在链接上，点击必须生效），两驱动通过 |

### g07 演示结果（面板有界面，11-14）
| 驱动 | 结果 | 步数 | 查看结果（成功 / 失败） | 留下的标签页 |
|---|---|---|---|---|
| CDP | 通过 | 59 | 4 / 3 | 3 个：第 1、2、3 页（经调试端口核对） |
| Playwright | 通过 | 76 | 6 / 6 | 按步骤日志推算 3 个（无调试端口，未直接核对） |

失败的都是 Google 购物 / 视频卡片（点击只在 Google 页内弹窗，不离开 Google），属于用例规则不够细，两驱动一致。

### DoorDash 诊断（11-14）
两种驱动打开 doordash.com 都停在 Cloudflare 验证页；用户手动点验证框后仍转圈或反复验证。临时去掉 Playwright 的 `OPEN_SHADOW_JS` 注入再试，结果相同，**已回滚**（回滚后 Playwright T01、T22 通过）。结论：不是我们的 Shadow DOM 设定导致；与 ver0 交接里“识别出 CDP 控制的浏览器、人工过验证也不放行”一致。按边界不做规避。

### Google“异常流量”分析（11-14，用户问：为什么 Playwright 那次没提示）

对照（每组从首页输入“unique toys”回车一次）：

| 组 | 驱动 / 用户目录 | webdriver | UA | 结果 |
|---|---|---|---|---|
| 基线 | CDP / ver0（不搜索） | true | Chrome/157.0.0.0 | — |
| A | Playwright / 全新空目录 | true | 同上 | 被拦（跳 `google.com/sorry/`） |
| B | Playwright / ver0（P20 刚清过 Cookie） | true | 同上 | 被拦 |

当天时间线：04:44–05:00 两驱动都没被拦 → 06:14 CDP 被拦、人工通过 → P20 清空 ver0 Cookie → 07:03 Playwright 被拦、连续两次人工通过 → 07:15–07:17 Playwright 没被拦 → P20 再清空 → 现在两组都被拦。

结论：不是驱动差异。两驱动对 Google 呈现的 `navigator.webdriver`、UA、用户目录完全一样。被不被拦取决于 ① 这个 IP 当天的搜索量（短时间多次搜索后被盯上），② 用户目录里有没有人工通过验证后留下的状态——人工过一次后，同一目录接下来一段时间不论哪个驱动都不被拦；面板验收 P20 清空 Cookie 后又被拦。未取得的直接证据：人工通过后目录里新增的具体 Cookie 名（本次只记了通过前的：AEC、NID、SEARCH_SAMESITE、__Secure-STRP）。按边界只观察记录，不据此做规避。

## 已知差异（经用户确认）

| 项 | 现象 | 原因 | 用户决定（11-14） |
|---|---|---|---|
| T17 Python 内存（Playwright 驱动） | 11-14 首次全量：50 轮 550/550 通过，但 Python 内存第 5→50 轮 59→75 MB（+27%，标准 ≤ 20%）；Chrome 内存 +12%。**11-14 第二次全量：60→68 MB（+13%），通过**——增长幅度随运行波动，处在标准附近 | Playwright 给每个请求 / 响应 / frame 在 Python 端登记对象，标签页关闭前不释放（每类上限 1 万个，增长有上限）。T17 直接反复打开网址、不经过用例开始，`fresh_start` 的换标签页回收覆盖不到 | 记为已知差异；只在用例开始时回收（不在打开网址时回收，以免影响“后退”） |

## 未查清的偶发问题

| 现象 | 已知情况 | 处理 |
|---|---|---|
| CDP 有界面：Google 页截图卡住 | 11-14 06:36–06:42 之间，CDP 有界面打开 Google 首页 / 搜索页后 `Page.captureScreenshot` 一直不返回（页面 `visibilityState=visible`、有焦点）；同一时段本地夹具有界面正常；之后连跑 2 次共 6 轮“进站 → 后退 → 截图”、4 种截图参数均正常，未能复现。Playwright 有界面同期只跑 1 次，正常 | 推测与当时电脑状态有关（如显示器短暂休眠、窗口最小化，有界面 Chrome 不出新帧），无证据，未加推测性的重试代码。再出现时记下时间和窗口状态 |

## 检查结果（11-14，最终回归在全部修改之后）

最终回归产物：CDP `runs/20261114-071727-acceptance`（20/20）；Playwright `runs/20261114-072128-acceptance-playwright`（20/20）。T17 50 轮（Playwright）见 `runs/20261114-054746-acceptance-playwright`。

| 项 | 结果 | 证据 |
|---|---|---|
| C1 单元测试 | 通过 | `pytest -q` 102 passed（改动前 77） |
| C2 默认配置一键验收 | 通过 | `run_tests.py --rounds 3`（CDP）20/20：T01–T17、T21–T23；目录名不带驱动后缀 |
| C3 默认配置面板验收 | 通过 | `run_panel_tests.py`（CDP）27/27，含新增 P31（最终复跑见下方“面板复跑”） |
| C4 核心依赖不变 | 通过 | `pyproject.toml` 的 `dependencies` 仍为 websockets / httpx / pillow / pyyaml；`playwright>=1.63` 只在可选依赖 `pw` |
| C5 未装 Playwright 时的提示 | 通过 | 无 Playwright 的临时环境：`--driver playwright` 退出码 2，提示“请运行：pip install -e .[pw]（使用本机 Chrome，无需执行 playwright install）”；默认驱动跑 t16 通过 |
| C6 未装时导入不报错 | 通过 | 同一环境 `import hands.runner, hands.driver` 无异常 |
| C7 配置文件选驱动 | 通过 | `driver: playwright` 不带参数 → 日志“驱动: playwright”，t16 通过；改回 `cdp` → “驱动: cdp”；`config.yaml` 已复原 |
| C8 命令行优先 | 通过 | 配置为 cdp 时 `--driver playwright` → “驱动: playwright”，t16 通过 |
| C9 非法取值 | 通过 | `--driver foo` → argparse 报 invalid choice（退出码 2）；`driver: foo` → `ValueError: 未知驱动 driver='foo'，可选：cdp / playwright` |
| C10 Playwright 一键验收 | 通过 | 50 轮全量 19/19（T17 550/550，内存 +13%）；最终 `--rounds 3` 20/20 |
| C11 本机 Chrome、同一用户目录 | 通过 | 运行中主进程 `C:\Program Files\Google\Chrome\Application\chrome.exe`、`--user-data-dir=…\profiles\ver0`、`--remote-debugging-pipe`。`%LOCALAPPDATA%\ms-playwright` 下的内核建于 2026-10-10（与 playwright 1.63.0 安装同时，早于本项目），本方案未下载、未使用 |
| C12 不去掉自动化标记 | 通过 | `navigator.webdriver` = true（命令行无 `--enable-automation`，由 Playwright 经协议设置） |
| C13 有界面 / 无界面 | 通过 | t16 `--driver playwright` 无界面、`--headed` 各一次通过 |
| C14 跨源 iframe | 通过 | T08（Playwright）：同源 / 跨源 / 影子按钮误差均 0.0 px |
| C15 等待与超时 | 通过 | T06 等待 2514 ms 出现 6 项；T07 5000 ms 返回 timeout，列出 `/api/delay?ms=8000` |
| C16 崩溃重跑 | 通过 | T02（Playwright）检测 0.33 s，crashes=1 |
| C17 新标签页与后退 | 通过 | T12（Playwright）通过 |
| C18 轨迹与干扰 | 通过 | T21（Playwright）17 次全中、跨源 iframe 命中、同种子一致 |
| C19 运行产物 | 通过 | `runs/20261114-065602-kbog`（Playwright t16）每步有 pagestate / decision / action / step / screenshot，report.html 链接各步截图；人工抽看 Playwright T05 标注图，16 个框与元素吻合。注：report.html 本身不画元素框（两驱动相同），框的核对用 T05 标注图 |
| C20 封闭 Shadow DOM | 通过 | T22 两驱动通过 |
| C21 [人工] 命令行转人工 | 通过 | `runs/20261114-070300-3i0q`：Playwright，转人工 1 次（text:人机验证），等人 14.9 s，之后判通过，报告记录起止时间。首次失败原因与修复见偏离表“用例时限不含等人时间” |
| C22 面板（Playwright） | 通过 | 26/26，P30 不适用 |
| C23 切换控件 | 通过 | 页面含驱动下拉 `driverSel`；切到 Playwright 后状态 running、driver=playwright（P31、E2）；预览帧率见 P05 |
| C24 有任务时不能切换 | 通过 | P31：任务中 `POST /api/browser/driver` → 409 |
| C25 预览与手动输入 | 通过 | P05 100.2 帧/秒；P14 转发点击 + “你好abc” 成功（Playwright） |
| C26 拒绝真实网站采集 | 通过 | Playwright 下 `POST /api/jobs {kind: sample_real}` → 400“Playwright 驱动不支持真实网站采集（请切换到 CDP 驱动）” |
| C27 保存为默认 | 通过 | 保存后 `config.yaml` 为 `driver: playwright`，注释 20→20 行保留；重启面板默认即 playwright；测后已还原 |
| C28 [人工] Google 用例 | 通过 | `runs/20261114-071650-qq1e`：g02 Playwright 有界面 8 步通过，点进 f-zd.com“樊振东百科”，读到“樊振东以 94% 得票率当选 2025/26 赛季德甲最佳球员”等内容；人工核对 step_007 截图。前一次（`20261114-070336-97ls`）Google 连出两次“异常流量”验证、用户人工通过后，暴露点击取点问题，见偏离表 |
| C29 性能对比 | 通过 | `docs/playwright/perf.md` |
| C30 上层不感知驱动 | 通过 | 指定文件无 `session.send`；`driver ==` 只在 `driver.py:78` |
| C31 hands 不依赖 panel | 通过 | `src/hands` 无 `import panel` / `from panel` |
| C32 无固定 sleep | 通过 | T06 源码扫描覆盖 `src/hands/pw/`，通过 |
| C33 坐标为 CSS 像素 | 通过 | T05、T08（Playwright）误差 0.0 px |
| E1 命令行切换 | 通过 | t16：CDP `20261114-065555-hyo6` 与 Playwright `20261114-065602-kbog` 均 passed、4 步 |
| E2 面板切换 | 通过 | t13_reddit_yes：CDP → Playwright → CDP 三次均通过（5 步），历史列表三条都在、报告都能打开 |

### 面板复跑（最终修改之后）

CDP 27/27（`runs/20261114-072629-panel-acceptance`）；Playwright 26/26，P30 不适用（`runs/20261114-073106-panel-acceptance-playwright`）。
