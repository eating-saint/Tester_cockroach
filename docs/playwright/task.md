# Playwright 驱动方案 Tasks

对应：`docs/playwright/spec.md`、`docs/playwright/plan.md`（均已批准）。
约定：每完成一组任务跑一次 `pytest -q`；动 CDP 代码的任务（W1–W7）完成后必须跑 CDP 回归，确认 N1。浏览器只用 `profiles/ver0`，测试不能并行。PowerShell 不支持 `&&`。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml` | 可选依赖 `pw = ["playwright>=1.63"]` |
| 修改 | `config.yaml`、`src/hands/config.py` | `driver` 配置项 |
| 新建 | `src/hands/driver.py` | `create_browser`、`DriverUnavailable`、`BrowserLike` / `PageLike` 协议 |
| 修改 | `src/hands/monitor.py` | 拆出记账层 |
| 修改 | `src/hands/page.py` | 抽出 `wait_quiet`；新增底层原语 |
| 修改 | `src/hands/actions.py`、`pagestate.py`、`screenshot.py`、`humanize.py`、`dom.py` | 改调页面原语 |
| 修改 | `src/hands/__main__.py` | `--driver` |
| 新建 | `src/hands/pw/__init__.py`、`browser.py`、`page.py`、`dom.py`、`session.py` | Playwright 驱动 |
| 修改 | `src/panel/service.py`、`preview.py`、`server.py`、`jobs.py`、`settings.py`、`static/index.html`、`static/app.js` | 面板接入与切换 |
| 新建 | `fixtures/f15_closed_shadow/index.html`、`expected.json` | 封闭 Shadow DOM 夹具 |
| 修改 | `tests/acceptance/common.py`、`t01_launch.py`、`t08_frames.py` | 驱动无关化 |
| 新建 | `tests/acceptance/t22_closed_shadow.py` | 封闭 Shadow DOM 验收 |
| 新建 | `tests/unit/test_monitor_ledger.py`、`test_driver_factory.py`、`test_pw_dom_parse.py` | 单元测试 |
| 修改 | `run_tests.py`、`run_panel_tests.py` | `--driver` |
| 新建 | `tools/bench_drivers.py`、`docs/playwright/perf.md` | 性能对比 |
| 新建 | `docs/playwright/acceptance.md` | 验收记录 |

---

## 第一组：配置与工厂

### W1: 依赖与配置项
**文件：** `pyproject.toml`、`config.yaml`、`src/hands/config.py`
**依赖：** 无
**步骤：**
1. `pyproject.toml` 的 `[project.optional-dependencies]` 加 `pw = ["playwright>=1.63"]`。
2. `Config` 加字段 `driver: str = "cdp"`；`load_config` 读 `config.yaml` 的 `driver`，新增参数 `driver: str | None = None`（非 None 时覆盖）；取值不在 `{"cdp", "playwright"}` 时抛 `ValueError`。
3. `config.yaml` 加 `driver: cdp`，带中文注释（cdp = 自写 CDP；playwright = Playwright，需 `pip install -e .[pw]`）。

**验证：** `python -c "from hands.config import load_config; print(load_config().driver, load_config(driver='playwright').driver)"` 输出 `cdp playwright`；`pytest -q` 全过。

### W2: 驱动工厂与协议
**文件：** `src/hands/driver.py`、`tests/unit/test_driver_factory.py`
**依赖：** W1
**步骤：**
1. 定义 `DriverUnavailable(RuntimeError)`，消息："已选择 Playwright 驱动，但未安装 playwright。请运行：pip install -e .[pw]（使用本机 Chrome，无需 playwright install）"。
2. 用 `typing.Protocol` 写 `BrowserLike`、`PageLike`，成员按 plan "浏览器接口 / 页面接口"两张表。
3. `create_browser(config)`：`cdp` → `BrowserManager(config)`；`playwright` → 在函数内 `import playwright`（ImportError → `DriverUnavailable`），再 `from .pw.browser import PwBrowserManager`。
4. `BrowserManager` 加只读属性 `driver = "cdp"`；`Page` 加类属性 `driver = "cdp"`。
5. 单元测试：cdp 返回 `BrowserManager`；用 `monkeypatch` 让 `import playwright` 失败时抛 `DriverUnavailable` 且消息含 `pip install -e .[pw]`。

**验证：** `pytest -q tests/unit/test_driver_factory.py` 通过。

### W3: 命令行 --driver
**文件：** `src/hands/__main__.py`
**依赖：** W2
**步骤：**
1. `run` 子命令加 `--driver {cdp,playwright}`，传给 `load_config(driver=...)`。
2. 所有 `BrowserManager(config)` 改为 `create_browser(config)`。
3. 捕获 `DriverUnavailable`：打印消息，以退出码 2 退出。

**验证：** `python -m hands run cases/t20_bing.yaml --help` 显示 `--driver`；在没装 playwright 的临时虚拟环境里（`python -m venv`，`pip install -e .`）运行 `python -m hands run cases/t16_report.yaml --driver playwright`，看到中文提示且 `$LASTEXITCODE` 为 2。

---

## 第二组：CDP 代码整理（行为不变）

### W4: Monitor 拆记账层
**文件：** `src/hands/monitor.py`、`tests/unit/test_monitor_ledger.py`
**依赖：** 无
**步骤：**
1. 新增公开方法 `req_started(key, url, rtype, frame_id, owner)`、`req_response(key, status, url, frame_id)`、`req_finished(key)`、`req_failed(key, error_text)`、`console_error(entry: ConsoleEntry)`、`set_lifecycle(name)`，把现有 `_on_request / _on_response / _on_finished / _on_failed / _on_lifecycle` 的函数体搬进来（忽略类型、`data:`/`blob:`、redirect、`_late` 计时、失败去重都在这里）。
2. 原 `_on_*` 只做参数解析后调用上述方法；`attach_session` 订阅不变。
3. 单元测试（直接调记账层，不开浏览器）：WebSocket 不计入；load 后发出的 Script 挂满 3 秒后 `blocking_count()` 归零；Document 不享受 3 秒规则；4xx 进 failed 且只记一次；redirect 不增加 pending。

**验证：** `pytest -q` 全过（含新测试）。

### W5: 抽出 wait_quiet
**文件：** `src/hands/page.py`
**依赖：** 无
**步骤：**
1. 新增模块级 `async def wait_quiet(monitor, loaded: asyncio.Event, config, timeout: float, setup_tasks=()) -> WaitResult`，内容为现有 `_wait_settled` 的算法（STATUS 调用一并搬入）。
2. `Page._wait_settled` 改为 `return await wait_quiet(self.monitor, self._loaded, self.config, timeout, self._setup_tasks)`。

**验证：** `python run_tests.py --only T06,T07,T11,T12` 全过。

### W6: Page 新增底层原语（CDP 版）
**文件：** `src/hands/page.py`
**依赖：** W5
**步骤：**
1. 从 `actions.py` 搬入：`locate_box(el)`（原 `_locate_box` 函数体）、`node_value(el)`（原 `_node_value`）。
2. 新增 `layout()`：一次 `Page.getLayoutMetrics`，返回 `{vw, vh, scroll_x, scroll_y, content_w, content_h}`（vw/vh = `cssLayoutViewport.clientWidth/Height`；scroll_y = `cssVisualViewport.pageY`，与现有 `_scroll_y` 一致；content = `cssContentSize`）。
3. 新增 `mouse_move(x, y)`、`mouse_click(x, y)`（pressed + released，clickCount 1）、`mouse_wheel(x, y, dy)`（mouseMoved + mouseWheel），参数与 actions 现有调用一致。
4. 新增 `key_press(name)`（原 `press` 里按 `KEYS` 表发 keyDown/keyUp 的代码，`KEYS` 留在 actions 并由 page 导入，避免循环导入则移到 `page.py`，actions 再从 page 导入）、`select_all()`（原 type 里的 Ctrl+A 两条）、`insert_text(text)`。
5. 新增 `screenshot(full_page=False)`（原 `screenshot.capture` 的函数体）、`bring_to_front()`、`panel_session()`（返回 `self.session`）。
6. 新增 `frame_info(el) -> ((ox, oy), cross_process)`、`evaluate_in_frame(el, expr)`：按 `el._ref.session_id` 找 FrameSession，返回其 `owner_offset` 与 `parent is not None`；在该会话求值。

**验证：** `python -c "import hands.page"` 无错误；`pytest -q` 全过。

### W7: 上层改调原语
**文件：** `src/hands/actions.py`、`pagestate.py`、`screenshot.py`、`humanize.py`、`dom.py`、`tests/unit/test_humanize.py`（如有调用 `move_to`）
**依赖：** W6
**步骤：**
1. `actions.py`：`_locate_box` → `self.page.locate_box(el)`；`_node_value` → `self.page.node_value(el)`；`_viewport` / `_scroll_y` / `_scroll_jitter` 的内容高度 → `self.page.layout()`；`_mouse_to` 的直接移动 → `page.mouse_move`；`_mouse_click` → `page.mouse_click`；`_wheel` → `page.mouse_wheel`；`type` → `page.select_all()` + `page.insert_text(text)`；`press` → `page.key_press(key)`（保留 `KEYS` 校验）。actions 里不再出现 `.session.send`。
2. `humanize.py`：`move_to(self, mouse_move, target, viewport)`，循环里 `await mouse_move(x, y)`；actions 传 `self.page.mouse_move`。
3. `pagestate.py`：视口用 `page.layout()`；`screenshot.capture(page, full_page)` 改为 `return await page.screenshot(full_page)`。
4. `dom.py`：`DomReader.read` 开头加 `if getattr(page, "driver", "cdp") != "cdp": return await page.read_dom()`。
5. `rg 'session.send' src/hands/actions.py src/hands/pagestate.py src/hands/screenshot.py src/hands/humanize.py` 无结果。

**验证：** `pytest -q` 全过；`python run_tests.py`（T01–T17、T21，CDP 驱动）全过；T21 轨迹点数与种子重放结果与改动前一致（同一 `--seed` 跑两次对比 report 中轨迹）。

---

## 第三组：Playwright 驱动

### W8: PwBrowserManager
**文件：** `src/hands/pw/__init__.py`、`src/hands/pw/browser.py`
**依赖：** W2
**步骤：**
1. `start()`：`mkdir` 用户目录；`profile_chrome_pids` + `kill_tree` 清残留；`self._pw = await async_playwright().start()`；`self.context = await self._pw.chromium.launch_persistent_context(str(profile_dir), executable_path=config.chrome_path, headless=self.headless, no_viewport=True, args=[f"--window-size={w},{h}", "--disable-session-crashed-bubble", "--hide-crash-restore-bubble", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", "--disable-background-timer-throttling", "--disable-features=BackForwardCache,CalculateNativeWinOcclusion"])`。
2. 重置 `crashed`、`crash_reason`、`crash_detected_at`、`_stopping`；`context.on("close", …)` 在非主动停止时 `_mark_crashed("browser closed")`；启动看门狗（`config.watchdog_interval_s`，检查 `pid` 进程是否还在，用 `psutil` 不可用时用 `profile_chrome_pids`）。
3. `pid` 属性：缓存启动后 `profile_chrome_pids` 中命令行不含 `--type=` 的进程。
4. `stop()`：`_stopping=True`，取消看门狗，`page.detach()`，`context.close()`（超时 5 秒），`_pw.stop()`，清残留进程。`restart()`、`relaunch(headless)` 同 CDP 版。
5. `new_page()`：`context.pages[0]` 或 `context.new_page()`，构造 `PwPage(self, p)`，`await page.attach()`。
6. 属性 `driver = "playwright"`、`port = None`、`version_info = {}`。

**验证：** 脚本启动 → `new_page()` → `page.evaluate("1+1") == 2` → `stop()`，之后 `profile_chrome_pids(profile)` 为空（W9 完成后执行；本任务先以 `context.pages` 非空验证）。

### W9: PwPage —— 事件接线与等待
**文件：** `src/hands/pw/page.py`
**依赖：** W4、W5、W8
**步骤：**
1. `attach()`：新建 `Monitor()`、`_loaded`；订阅 `request / response / requestfinished / requestfailed / console / pageerror / domcontentloaded / load / crash`；`document.readyState == "complete"` 时置 `_loaded`。`detach()` 取消全部订阅。
2. 请求 → 记账层：key 用 Request 对象；资源类型映射 `{"document": "Document", "eventsource": "EventSource", "websocket": "WebSocket", "ping": "Ping", "other": "Other"}`，其余首字母大写；有 `redirected_from` 时先 `req_finished(旧请求)`；主 frame 的导航请求（`is_navigation_request()` 且 `frame == page.main_frame`）清 `_loaded` 并 `set_lifecycle("init")`。
3. `response`：`req_response(req, status, url, None)`；`requestfinished` → `req_finished`；`requestfailed` → `req_failed(req, req.failure)`。
4. `console` 的 type 为 error/assert → `console_error(ConsoleEntry("console", "error", text))`；`pageerror` → `ConsoleEntry("exception", "error", str(err))`。`load` → `set_lifecycle("load")` + `_loaded.set()`；`crash` → `browser._mark_crashed("renderer crashed")`。
5. `wait_settled` → `wait_quiet(self.monitor, self._loaded, self.config, timeout)`。
6. `navigate`：`reset_for_navigation()`、清 `_loaded`；`await page.goto(url, wait_until="commit", timeout=timeout*1000)`；Playwright 超时 → 返回 `WaitResult("timeout", 已用时, monitor.pending_urls())`；锚点导航（goto 返回 None）置 `_loaded`；再 `wait_settled`。`reload` 同理用 `page.reload(wait_until="commit")`。
7. `_call(coro)` 统一转换异常：浏览器 `crashed` 已置位 → `BrowserCrashed(reason)`；其余 Playwright `Error` → `CDPError("playwright", -1, str(e))`。所有对外方法经它。
8. `evaluate(expr)` → `page.evaluate("s => (0, eval)(s)", expr)`（调用方传的是 CDP 风格的表达式字符串，如 `"document.title"`、`"(()=>{…})()"`；统一用间接 eval 按全局表达式求值，避免 Playwright 把字符串误判为函数）；`url()` → `page.url`；`title()` → `page.title()`；`wait_for` 复制 CDP 版（带 `# condition-poll` 标注）。

**验证：** `python run_tests.py --driver playwright --only T06,T07,T11`（W14 之前用临时脚本：导航 F3、F4、F9 夹具，打印 WaitResult 与 monitor 快照，确认 F3 列表已出现、F4 在 5 秒超时并列出慢请求、F9 有 404 与 `FIXTURE_ERROR_42`）。

### W10: PwPage —— 导航、标签页与原语
**文件：** `src/hands/pw/page.py`
**依赖：** W9
**步骤：**
1. 新标签页：`context.on("page", cb)`，cb 里 `await p.opener()` 是当前页时放入 `_new_tabs` 队列；`take_new_tab()` 非阻塞取出；`adopt_new_tab(p)`：记 `_openers`，`detach()`，换成新页，`attach()`，`wait_settled(True)`。
2. `back()`：`reset_for_navigation()`、清 `_loaded`，`r = await page.go_back(wait_until="commit")`；`r is None` 且 `page.url` 未变 → 若有 `_openers`：关闭当前页、回到打开者、`attach()`、`wait_settled(False)`；否则 `WaitResult("no_history")`。
3. `layout()`：一次 `evaluate`，返回 `{vw: documentElement.clientWidth, vh: documentElement.clientHeight, scroll_x: scrollX, scroll_y: scrollY, content_w: documentElement.scrollWidth, content_h: documentElement.scrollHeight}`。
4. 鼠标：`mouse_move` → `page.mouse.move(x, y)`；`mouse_click` → `move` + `down()` + `up()`；`mouse_wheel` → `move(x, y)` + `page.mouse.wheel(0, dy)`。键盘：`key_press(name)` → `page.keyboard.press(name)`（`KEYS` 中的名字与 Playwright 键名一致：Enter、Tab、Escape、Backspace、ArrowDown、ArrowUp、PageDown、End）；`select_all` → `keyboard.press("Control+A")`；`insert_text` → `keyboard.insert_text`。
5. `screenshot(full_page)` → `page.screenshot(full_page=full_page, type="png")`；`bring_to_front()` → `page.bring_to_front()`；`refresh_offsets()` 空操作。
6. `panel_session()`：懒创建 `PwCdpSession(await context.new_cdp_session(page))`（见 W12），换页 / detach 时 `detach()` 并清空。

**验证：** 临时脚本在 F11 夹具（导航）上：点开新标签页链接后 `take_new_tab()` 非空、`adopt_new_tab` 后 `url()` 为新页；`back()` 回到原页；`layout()` 数值与 `evaluate("innerWidth")` 合理。

### W11: PwDomReader
**文件：** `src/hands/pw/dom.py`、`src/hands/pw/page.py`、`src/hands/pw/browser.py`、`tests/unit/test_pw_dom_parse.py`
**依赖：** W10
**步骤：**
1. `OPEN_SHADOW_JS`：替换 `Element.prototype.attachShadow`，把参数的 `mode` 改为 `open`；`PwBrowserManager.start()` 中 `context.add_init_script(OPEN_SHADOW_JS)`。
2. `parse_snapshot(text) -> list[Entry]`（纯函数）：逐行解析 `- role "name" [attr]… [ref=xx] [box=x,y,w,h]`，得到 role、name（处理转义引号）、ref、disabled、所属 frame 前缀（ref 去掉末尾 `e数字` 的部分，主 frame 为空串）。单元测试覆盖：带名字 / 不带名字、`[disabled]`、`[cursor=pointer]`、iframe 子项 `f1e2`、名字里含引号。
3. `EXTRA_JS`（`e => {...}`）返回 `{tag, href(绝对), src, value, disabled, rect:[x,y,w,h], inShadow: e.getRootNode() instanceof ShadowRoot, text(折叠空白、前 300 字), context, sx: scrollX, sy: scrollY}`；context 按 `_Doc.context_of` 同一算法：从父元素起最多向上 6 层，遇 BODY/HTML 停，取文字长度 ≤ 600 的最长一层。
4. `read(pwpage)`：`snap = await page.aria_snapshot(mode="ai", boxes=True)`；筛选 role ∈ `INTERACTIVE_ROLES ∪ {"iframe"}`；`asyncio.gather` 对每个 ref 执行 `page.locator(f"aria-ref={ref}").evaluate(EXTRA_JS)`（单个失败则跳过该元素）。
5. frame 偏移：对每个出现的 frame 前缀，取该 frame 内第一个元素的 `element_handle().owner_frame()`，再 `frame.frame_element()` → `bounding_box()` 加 `clientLeft + paddingLeft`、`clientTop + paddingTop`；结果按前缀缓存（本次读取内）。主 frame 偏移 (0, 0)。
6. 组装 `Element`：box = rect + 偏移；`visible = w > 0 and h > 0 and rect.x + sx + w > 0 and rect.y + sy + h > 0`；`in_viewport = visible and box ∩ (0,0,vw,vh)`；`enabled = not (snapshot disabled or js disabled)`；name 空时按 CDP 版同一后备顺序（text → placeholder → title → name 属性 → value）；iframe 的 name = title 或 name 属性、href = src；`frame_path` = 前缀列表（如 `["f1"]`）；`_ref = PwRef(prefix, ref)`；`context` 仅 visible 且非 iframe 时取 JS 结果，否则 = text。依序编号 `e0…`。
7. 正文：对 `page.frames` 依次 `frame.evaluate("document.body ? document.body.innerText : ''")`，失败跳过，`\n` 拼接。
8. `PwPage.read_dom()` 调它；`PwPage.locate_box(el)`：`loc = page.locator(f"aria-ref={el._ref.aria_ref}")`，`scroll_into_view_if_needed(timeout=…)`，`bounding_box()`；为 None 或抛错时重读一次元素列表，按 (role, name, frame 前缀) 找同一元素再试，仍失败抛 `LookupError`。`node_value(el)` → `loc.evaluate("e => 'value' in e ? e.value : (e.isContentEditable ? e.innerText : null)")`。`frame_info(el)` → (该 frame 偏移, 前缀非空 且 frame 源与主页面不同源)；`evaluate_in_frame(el, expr)` → 在 owner_frame 中求值。

**验证：** `pytest -q tests/unit/test_pw_dom_parse.py` 通过；临时脚本在 F1、F5、F6 夹具上打印元素：F1 可见元素按角色计数为 button 5 / textbox 3 / link 8；F5 两个按钮坐标与页面实测误差 ≤ 1 px。

### W12: 面板专用 CDP 会话包装
**文件：** `src/hands/pw/session.py`
**依赖：** W8
**步骤：**
1. `PwCdpSession(raw)`：`session_id` = `f"pw-{id(raw)}"`；`async send(method, params=None, timeout=None)` → `asyncio.wait_for(raw.send(method, params or {}), timeout or 30)`，异常转 `CDPError`；`on(method, cb)` / `off(method, cb)` → `raw.on` / `raw.remove_listener`；`detach()` → `raw.detach()`（忽略错误）。

**验证：** 临时脚本：`s = await page.panel_session()`；`await s.send("Page.getLayoutMetrics")` 返回含 `cssLayoutViewport`；`on("Page.screencastFrame")` 后 `send("Page.startScreencast")` 能收到帧。

---

## 第四组：验收脚本与夹具

### W13: 封闭 Shadow DOM 夹具与 T22
**文件：** `fixtures/f15_closed_shadow/index.html`、`expected.json`、`tests/acceptance/t22_closed_shadow.py`、`run_tests.py`
**依赖：** W7、W11
**步骤：**
1. 夹具：自定义元素 `<closed-box>` 在 constructor 里 `attachShadow({mode: "closed"})`，内含按钮"封闭影子按钮"，点击后把页面 `#result` 文字设为 `closed-ok`；页面再放一个普通按钮做对照。
2. `expected.json`：`{"button": {"name": "封闭影子按钮", "result": "closed-ok"}}`。
3. T22：导航 → 读元素 → 找到 name 匹配、`in_shadow` 为真的元素 → `ActionExecutor(page).click(e)` → `#result` 为 `closed-ok`。
4. `run_tests.py` 的默认清单加入 T22。

**验证：** `python run_tests.py --only T22`（CDP）通过。

### W14: 验收脚本加 --driver，T01 / T08 驱动无关化
**文件：** `run_tests.py`、`tests/acceptance/common.py`、`t01_launch.py`、`t08_frames.py`
**依赖：** W7、W11
**步骤：**
1. `run_tests.py` 加 `--driver`，传给 `Ctx(driver=…)`；`Ctx` 中 `load_config(..., driver=driver)`，`BrowserManager(self.config)` → `create_browser(self.config)`；输出表头注明驱动；结果目录名加 `-<driver>` 后缀（CDP 时保持原名，N1）。
2. T01：`create_browser(ctx.config)`；`b.driver == "cdp"` 时逻辑不变；否则每次检查 `(await b.new_page()).evaluate("1+1") == 2`，其余（10 次、无残留）相同。
3. T08：用 `page.frame_info(e)` 取偏移和是否独立进程，用 `page.evaluate_in_frame(e, RECT % …)` 取跨源按钮实测框；"跨源 iframe 是否有独立会话"改为对跨源按钮检查 `frame_info(e)[1]` 为真。同源分支不变。
4. T06 的固定 sleep 扫描范围加入 `src/hands/pw/`。

**验证：** `python run_tests.py`（CDP）T01–T17、T21、T22 全过且耗时与此前同量级；`python run_tests.py --driver playwright --only T01,T03,T04,T05` 通过。

### W15: Playwright 全量验收与修正
**文件：** 按失败项定位到 `src/hands/pw/*`
**依赖：** W14
**步骤：**
1. `python run_tests.py --driver playwright`，逐项记录结果到 `docs/playwright/acceptance.md`。
2. 失败项先看该用例的 report.html 与截图定位原因，在 `pw/` 内修正（不得改动 CDP 驱动行为；如需改共用代码，改完重跑 CDP 全量）。
3. 重点核对：T02（kill `browser.pid` 后 5 秒内检测、重跑通过）、T06/T07（等待）、T08（跨源坐标 ≤ 1 px）、T10/T21（滚动、滚轮乱动回滚误差）、T12（后退触发 load；若不触发，检查 `--disable-features` 是否被 Playwright 覆盖，合并为一条）、T17（50 轮）。
4. 滚轮相关项若确认是 Playwright 本身限制，记为已知差异，向用户说明并请其确认（spec AC2）。

**验证：** `python run_tests.py --driver playwright` 中 T01–T17、T21、T22 全过（或仅有用户确认过的已知差异）。

### W16: 假验证码与 Google 用例
**文件：** 无新文件；结果写入 `docs/playwright/acceptance.md`
**依赖：** W15
**步骤：**
1. `python -m hands run cases/extra_captcha.yaml --driver playwright`：出现转人工提示，切到有界面；在 Chrome 窗口里处理后回车，用例继续并通过。
2. `python -m hands run cases/g02_fanzhendong.yaml --driver playwright --headed`：跑通（遇 reCAPTCHA 人工处理）。

**验证：** 两个用例的 report.html 判定为通过；第 2 项需用户在旁人工处理时执行。

---

## 第五组：面板

### W17: 面板后台接入工厂与 panel_session
**文件：** `src/panel/service.py`、`preview.py`、`server.py`、`jobs.py`
**依赖：** W12
**步骤：**
1. `service.py`：三处 `BrowserManager(self.config)` → `create_browser(self.config)`；捕获 `DriverUnavailable` 时状态置 stopped 并把消息放进状态里的 `error`。调到前台改为 `await b.page.bring_to_front()`；窗口摆放（`Browser.getWindowForTarget` / `setWindowBounds`）只在 `b.driver == "cdp"` 时执行。布局度量（约 245 行）改用 `page.layout()`；`page.session is None` 的判断改为 `page is None`。
2. `preview.py`：取会话改为 `sess = await page.panel_session()`；`b.conn.on/off("Page.screencastFrame", cb, sid)` 改为 `sess.on/off("Page.screencastFrame", cb)`。
3. `server.py`：手动输入转发 `svc.input.handle(await svc.browser.page.panel_session(), m)`。
4. `jobs.py`：布局度量改用 `page.layout()`；真实网站采集入口在 `browser.driver != "cdp"` 时返回错误"Playwright 驱动不支持真实网站采集"（HTTP 400）。

**验证：** `python run_panel_tests.py`（CDP）全过（与此前 26/26 一致）。

### W18: 面板切换驱动
**文件：** `src/panel/service.py`、`server.py`、`settings.py`、`static/index.html`、`static/app.js`
**依赖：** W17
**步骤：**
1. `service.set_driver(name)`：校验取值；有任务运行时抛与 `set_mode` 相同的"忙"错误（409）；否则停当前浏览器、`config.driver = name`、`browser_start()`。`browser_state()` 增加 `"driver"`。
2. `server.py`：`POST /api/browser/driver`，body `{"driver": "cdp" | "playwright"}`，经 `guarded` 调 `set_driver`。
3. `settings.py`：`driver` 纳入"保存为默认"写回 `config.yaml` 的字段。
4. 前端：浏览器控制区加"驱动"下拉（CDP / Playwright），选择后调接口；任务运行或 `status != idle/running` 切换中时禁用；状态栏显示"驱动：CDP / Playwright"；接口返回错误时显示消息。

**验证：** `python -m panel --no-window --port 8790`，浏览器打开面板：下拉切到 Playwright，状态栏显示 Playwright、预览有画面；运行 `panel_stop` 用例期间下拉为禁用，直接 POST 接口返回 409。

### W19: 面板验收脚本加 --driver
**文件：** `run_panel_tests.py`、`tests/panel/checks.py`（如需）
**依赖：** W18
**步骤：**
1. 加 `--driver`，启动面板服务前设置 `config.driver`；`--driver playwright` 时跳过 P30 并在表中标"不适用"。
2. 新增 P31：任务运行中 `POST /api/browser/driver` 返回 409；空闲时切换后 `state.browser.driver` 变为新值、`status` 回到 running。
3. `python run_panel_tests.py --driver playwright`，结果写入 `docs/playwright/acceptance.md`；失败项在 `pw/` 或面板代码中修正后，CDP 面板验收重跑一次。

**验证：** `python run_panel_tests.py --driver playwright` 除 P30 外自动项全过；`python run_panel_tests.py`（CDP）仍全过。

---

## 第六组：性能对比与收尾

### W20: 性能对比
**文件：** `tools/bench_drivers.py`、`docs/playwright/perf.md`
**依赖：** W15
**步骤：**
1. 对每种驱动：启动耗时（`start` + `new_page`，10 次取中位数）；F1 与 F10 夹具上 `build_page_state` 耗时（各 20 次中位数）；F1 上一次 `ActionExecutor.click` 耗时（20 次中位数，不含等待的部分与含等待分别记）；推流帧率（面板会话 `Page.startScreencast` 10 秒计帧）。
2. 读取两次 `run_tests.py --only T17` 输出中的总耗时与内存变化（CDP、Playwright 各一次）。
3. 生成 Markdown 表写入 `docs/playwright/perf.md`，注明机器与 Chrome 版本。

**验证：** `python tools/bench_drivers.py` 运行结束，`perf.md` 中两列数值齐全。

### W21: 回归与文档
**文件：** `docs/playwright/acceptance.md`、`docs/playwright/handover.md`、`README.md`
**依赖：** W16、W19、W20
**步骤：**
1. CDP 最终回归：`pytest -q`、`python run_tests.py --rounds 3`、`python run_panel_tests.py`，结果记入 acceptance.md。
2. 更新 handover.md 的"当前状态"；README 加一行 `--driver playwright` 用法与安装命令。

**验证：** 三项命令全过；acceptance.md 中每条 AC 都有结果与证据路径。

---

## 执行顺序

```
W1 → W2 → W3
W4 ─┐
W5 → W6 → W7 ──────────────┐
W2 → W8 → W9(需W4,W5) → W10 → W11 → W13 → W14 → W15 → W16
       └→ W12 ──────────────────────→ W17 → W18 → W19
                                         W15 → W20
                               W16, W19, W20 → W21
```
建议实际顺序：W1 → W2 → W3 → W4 → W5 → W6 → W7（CDP 回归）→ W8 → W9 → W10 → W11 → W12 → W13 → W14 → W15 → W16 → W17 → W18 → W19 → W20 → W21。
