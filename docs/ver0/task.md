# ver0 无头版"手脚" Tasks

> 依据：spec.md、plan.md。所有命令在 `D:\work\Bing_browse` 下执行。
> 编号约定：K1…K49 是开发任务；T01…T20 是 spec 中的验收测试编号。
> 夹具服务默认端口 8765；下文 `BASE=http://127.0.0.1:8765`，`XBASE=http://localhost:8765`（跨源）。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 打包 src/hands，声明依赖 |
| 新建 | `config.yaml` | 全局配置样例 |
| 新建 | `src/hands/__init__.py`、`__main__.py` | 包入口、CLI |
| 新建 | `src/hands/config.py` | Config、load_config |
| 新建 | `src/hands/geometry.py` | Box、坐标换算 |
| 新建 | `src/hands/cdp.py` | CDPConnection、CDPSession、CDPError、BrowserCrashed |
| 新建 | `src/hands/browser.py` | BrowserManager |
| 新建 | `src/hands/monitor.py` | Monitor、MonitorState、RequestInfo、ConsoleEntry |
| 新建 | `src/hands/page.py` | Page、FrameSession、WaitResult |
| 新建 | `src/hands/dom.py` | DomReader、Element、NodeRef |
| 新建 | `src/hands/screenshot.py` | capture、annotate |
| 新建 | `src/hands/pagestate.py` | PageState、build_page_state |
| 新建 | `src/hands/actions.py` | ActionExecutor、ActionResult |
| 新建 | `src/hands/decision.py` | TOOL_REGISTRY、Decision、validate_decision、Decider、DecisionInput、DecisionInvalid |
| 新建 | `src/hands/rule_decider.py` | RuleDecider、Rule |
| 新建 | `src/hands/tasktree.py` | TaskTree、TaskNode、TaskView、TreeEvent |
| 新建 | `src/hands/captcha.py` | detect、handoff |
| 新建 | `src/hands/recorder.py` | RunRecorder、StepRecord |
| 新建 | `src/hands/runner.py` | CaseSpec、Expect、CaseResult、load_case、CaseRunner |
| 新建 | `fixtures/server.py` | 夹具 HTTP 服务 |
| 新建 | `fixtures/f01_static/` … `fixtures/f11_nav/`、`fixtures/extra/gate.html` | 假网页 + expected.json |
| 新建 | `cases/*.yaml` | t02、t13×2、t14、t16、t20 用例 |
| 新建 | `tests/unit/test_*.py` | 纯逻辑单元测试 |
| 新建 | `tests/acceptance/common.py`、`t01_*.py` … `t17_*.py` | 验收测试 |
| 新建 | `run_tests.py` | 一键验收，输出表格 |

---

## 阶段 A：骨架与纯逻辑

## K1: 项目骨架

**文件：** `pyproject.toml`、`src/hands/__init__.py`、`config.yaml`、目录 `fixtures/ cases/ tests/unit/ tests/acceptance/ profiles/ runs/`
**依赖：** 无
**步骤：**
1. 建立上述目录；`profiles/`、`runs/` 放 `.gitkeep`。
2. `pyproject.toml`：setuptools，`package-dir = {"" = "src"}`，包名 `hands`，依赖 websockets、httpx、pillow、pyyaml；可选依赖 test = pytest、pytest-asyncio；`requires-python >= 3.11`；pytest 配置 `asyncio_mode = "auto"`、`testpaths = ["tests/unit"]`。
3. `src/hands/__init__.py` 只写 `__version__ = "0.0.1"`。
4. `config.yaml` 写入 plan 中 Config 全部字段的默认值（chrome_path 留空表示自动查找）。
5. `pip install -e .`

**验证：** `python -c "import hands; print(hands.__version__)"` 输出 `0.0.1`。

## K2: 配置加载

**文件：** `src/hands/config.py`
**依赖：** K1
**步骤：**
1. 定义 dataclass `Config`，字段与默认值同 plan（chrome_path、profile_dir、headless、window、quiet_ms、nav_timeout_s、cmd_timeout_s、watchdog_interval_s、max_crash_reruns、min_interval_s、captcha_markers、runs_dir）。captcha_markers 默认：`["captcha", "challenge", "recaptcha", "hcaptcha", "turnstile", "verify you are human", "验证您是人类", "人机验证"]`。
2. `find_chrome()`：依次检查 `%ProgramFiles%`、`%ProgramFiles(x86)%`、`%LocalAppData%` 下 `Google\Chrome\Application\chrome.exe`，找不到抛 FileNotFoundError。
3. `load_config(path="config.yaml", **overrides) -> Config`：读 YAML（文件不存在则全用默认），合并 overrides；路径字段转为相对项目根的绝对 Path；chrome_path 为空时调用 find_chrome。

**验证：** `python -c "from hands.config import load_config; c=load_config(); print(c.chrome_path, c.headless, c.quiet_ms)"` 打印 chrome.exe 真实路径、`True`、`500`。

## K3: 坐标工具

**文件：** `src/hands/geometry.py`、`tests/unit/test_geometry.py`
**依赖：** K1
**步骤：**
1. dataclass `Box(x, y, width, height)`：`center() -> (float, float)`、`contains(x, y)`、`offset(dx, dy) -> Box`、`intersects(other) -> bool`、`to_dict()`。
2. `quad_to_box(quad: list[float]) -> Box`（CDP 8 值四边形 → 外接矩形）。
3. `css_to_image(box, dpr) -> Box`（四个值都乘 dpr）。
4. `accumulate_offset(offsets: list[tuple[float, float]]) -> tuple[float, float]`。
5. 单元测试覆盖以上每个函数，至少各 1 个用例，含 dpr=1.25。

**验证：** `pytest tests/unit/test_geometry.py -q` 全部通过。

## K4: 感知层数据结构

**文件：** `src/hands/dom.py`、`src/hands/monitor.py`、`src/hands/pagestate.py`（此任务只写数据类）
**依赖：** K3
**步骤：**
1. `dom.py`：dataclass `NodeRef(session_id: str | None, backend_node_id: int)`；dataclass `Element`（字段同 plan，`_ref` 用 `field(repr=False)`）；`Element.to_dict()` 不含 `_ref`。
2. `monitor.py`：dataclass `RequestInfo(url, started_at, frame_id=None, status=None, error_text=None)`、`ConsoleEntry(source, level, text, url=None)`、`MonitorState(lifecycle, pending, failed, console_errors)`，均带 `to_dict()`。
3. `pagestate.py`：dataclass `PageState(url, title, viewport: Box, device_pixel_ratio, elements, body_text, monitor, screenshot, captured_at)`；`to_dict()`、`to_json(path)`（UTF-8，`ensure_ascii=False`，缩进 2）；`element(id) -> Element | None`。

**验证：** `python -c` 构造一个含 2 个 Element 的 PageState 并 `to_json('runs/_t4.json')`，打开文件确认字段齐全且没有 `_ref`。

## K5: 决策协议与校验

**文件：** `src/hands/decision.py`、`tests/unit/test_decision.py`
**依赖：** K4
**步骤：**
1. `TOOL_REGISTRY: dict[str, ToolSpec]`，ToolSpec 记录 `kind`（tree/action/read）、必填参数、可选参数、参数类型；内容同 plan 工具表。scroll 要求 `to_bottom: true` 或 `direction` + `pixels` 二选一。
2. dataclass `Decision(decision, reason, tool, arguments)`；异常 `DecisionInvalid(msg)`。
3. `validate_decision(raw: str) -> Decision`：JSON 解析失败、缺字段、decision≠"call_tool"、tool 未注册、缺必填参数、多余未知参数、类型不符 → 抛 DecisionInvalid，消息写明原因。
4. dataclass `DecisionInput(page: PageState, task: "TaskView", inputs: dict)`；`Decider` 为 `typing.Protocol`，含 `async def decide(self, inp) -> str`。
5. `async def decide_with_retry(decider, inp, max_tries=3) -> tuple[Decision | None, list[str], int]`：最多调用 3 次，返回（合法决策或 None、所有原始文本、尝试次数）。
6. 单元测试：合法 click；非 JSON；未注册工具；缺参数；scroll 两种合法形式与一种非法；用假 Decider 前两次返回非法、第三次合法 → 尝试 3 次成功；三次都非法 → 返回 None。

**验证：** `pytest tests/unit/test_decision.py -q` 全部通过。

## K6: 任务树

**文件：** `src/hands/tasktree.py`、`tests/unit/test_tasktree.py`
**依赖：** K1
**步骤：**
1. dataclass `TaskNode`、`TreeEvent`、`TaskView`（字段同 plan；TaskView.recent_steps 为 list[dict]，只含 tool/arguments/result 摘要）。
2. `TaskTree(root_goal, root_done_when, max_depth, max_steps)`：构造时建 root（depth 0、running），记录 create 事件。
3. `create_child(goal, done_when) -> dict`：活动节点 depth+1 > max_depth 时返回 `{"ok": False, "error": "max_depth"}`；否则建节点、设为活动、返回 `{"ok": True, "node_id": ...}`。
4. `complete(summary)` / `fail(reason)`：结束活动节点，context 置 None，向父节点 child_summaries 追加 `{goal, status, summary}`，父节点成为活动；结束的是 root 则 `finished=True`。
5. `record_step(step: dict)`：追加到活动节点 context（并供 recent_steps 取最近 5 条）。
6. `tick_step()`：活动节点 steps_used += 1；达到 max_steps 时调用内部 `_force_fail("step_limit")`（事件 kind=force_fail）。
7. `view() -> TaskView`；`events_json()`；`shape() -> list[str]`（如 `["root", "root/读取评论"]`）。
8. 单元测试：建子 → 完成 → 父节点 child_summaries 含摘要且子节点 context 为 None；超深度返回错误；步数达上限强制失败并回到父节点；root 完成后 finished；同一时间只有一个 running 节点在接收 record_step。

**验证：** `pytest tests/unit/test_tasktree.py -q` 全部通过。

## K7: 规则决策器

**文件：** `src/hands/rule_decider.py`、`tests/unit/test_rule_decider.py`
**依赖：** K5、K6
**步骤：**
1. dataclass `Rule(when: dict, do: dict)`；`RuleDecider(rules, default=None)`，实现 Decider。
2. 条件函数逐个实现：node_depth、goal_is、url_contains、text_contains（查 body_text）、element（对 visible 元素按 role 精确、name_contains/text_contains/href_contains 子串匹配，记下首个命中 id 作为 match）、no_element、has_child_summary、no_child_with_goal、last_tool、last_effective、read_count（当前节点 recent_steps 中 read_* 次数）。未知条件名 → 构造时抛 ValueError。
3. 模板渲染：对 `do.arguments` 中字符串替换 `{{inputs.xxx}}`、`{{match}}`、`{{last_read}}`（最近一次 read_* 结果；列表转为 `\n` 连接的文本）。
4. `decide()`：按序匹配，返回 `json.dumps({"decision":"call_tool","reason":"rule#i matched","tool":...,"arguments":...}, ensure_ascii=False)`；无命中用 default；都没有返回 fail("no rule matched")。
5. 单元测试：用手工构造的 PageState/TaskView 测 element 命中与 match 渲染、no_element、规则顺序优先、无命中走 default、未知条件报错。

**验证：** `pytest tests/unit/test_rule_decider.py -q` 全部通过。

## K8: 验证码检测

**文件：** `src/hands/captcha.py`（本任务只写 detect）、`tests/unit/test_captcha.py`
**依赖：** K4
**步骤：**
1. `detect(state: PageState, markers: list[str]) -> str | None`：不区分大小写，依次检查 url、body_text、tag 为 iframe 的元素的 name/href；命中返回描述字符串（如 `"url:captcha"`），否则 None。
2. 单元测试：url 命中、正文中文命中、iframe 命中、正常页不命中。

**验证：** `pytest tests/unit/test_captcha.py -q` 全部通过。

---

## 阶段 B：测试夹具

> 每个 expected.json 顶层至少含 `"fixture"` 与本页要核对的标准答案。

## K9: 夹具服务器

**文件：** `fixtures/server.py`
**依赖：** K1
**步骤：**
1. 基于 `ThreadingHTTPServer` + `SimpleHTTPRequestHandler`（目录 = fixtures/），禁用缓存头。
2. 路由：`/api/delay?ms=N&data=X` 睡 N 毫秒后返回 JSON `{"data": X}`；`/missing` 及不存在的文件返回 404；`/api/page?n=K` 返回第 K 页列表 JSON（给 F8）。
3. 同时在 `127.0.0.1:PORT` 和 `[::1]:PORT` 各起一个服务（IPv6 失败时打印警告继续）。
4. 提供 `start(port=8765) -> handle`（后台线程）、`handle.stop()`；`python fixtures/server.py` 前台运行。

**验证：** 启动后执行 `python -c "import httpx,time;t=time.time();print(httpx.get('http://127.0.0.1:8765/api/delay?ms=1000&data=a').json(), round(time.time()-t,1)); print(httpx.get('http://localhost:8765/missing').status_code)"`，输出 `{'data': 'a'} 1.0` 左右与 `404`。

## K10: F1 静态页、F2 隐藏元素页

**文件：** `fixtures/f01_static/index.html`、`expected.json`、`fixtures/f02_hidden/index.html`、`expected.json`
**依赖：** K9
**步骤：**
1. F1：5 个 `<button>`、3 个 `<input type=text>`、8 个 `<a href>`，每个带唯一 `id`；布局固定（绝对定位，部分在首屏以下）。expected：`{"counts":{"button":5,"textbox":3,"link":8},"ids":[...]}`。
2. F2：每个元素带唯一 id：2 个 `display:none` 按钮、2 个 `visibility:hidden` 按钮、2 个 `disabled` 按钮（可见）、2 个 `left:-9999px` 按钮、2 个正常按钮。expected：`{"elements":{"<id>":{"visible":bool,"enabled":bool},...}}`（display:none 的元素期望"不出现在列表中或 visible=false"，在 expected 里用 `"visible": false` 表示，校验时两种都算对）。

**验证：** 浏览器打开 `BASE/f01_static/index.html`、`BASE/f02_hidden/index.html`，人工看页面与 expected 描述一致。

## K11: F3 动态加载、F4 慢请求

**文件：** `fixtures/f03_dynamic/`、`fixtures/f04_slow/`
**依赖：** K9
**步骤：**
1. F3：脚本在 DOMContentLoaded 时立即 `fetch('/api/delay?ms=2000&data=...')`，返回后渲染 `<ul id=list>` 含 6 个 `<li><a href>`。expected：`{"list_links": 6}`。
2. F4：加载时 `fetch('/api/delay?ms=8000&data=slow')`。expected：`{"slow_url_contains": "/api/delay?ms=8000"}`。

**验证：** 浏览器打开 F3，约 2 秒后出现 6 个链接；打开 F4，开发者工具 Network 看到 8 秒的请求。

## K12: F5 iframe（同源 + 跨源）、F6 Shadow DOM

**文件：** `fixtures/f05_iframe/index.html`、`same.html`、`cross.html`、`expected.json`、`fixtures/f06_shadow/index.html`、`expected.json`
**依赖：** K9
**步骤：**
1. F5 主页面：顶部留 100px 偏移；`<iframe id=same src="same.html">` 与 `<iframe id=cross src="http://localhost:8765/f05_iframe/cross.html">`，两者都有 border 和 padding（检验内容框偏移）。主页面有 `<div id=result>`。
2. same.html / cross.html 各有一个按钮（`id=btn-same` / `btn-cross`，文本"同源按钮"/"跨源按钮"），点击后在自身页面显示 `clicked:same` / `clicked:cross`，并对 `parent` 发 `postMessage`；主页面监听消息写入 `#result`。
3. F6：自定义元素 `<x-panel>`，open shadow root 内含按钮 `id=btn-shadow` 文本"影子按钮"，点击后在 shadow 内显示 `clicked:shadow` 并写入主文档 `#result`。
4. expected：F5 `{"buttons":[{"name":"同源按钮","result":"clicked:same"},{"name":"跨源按钮","result":"clicked:cross"}]}`；F6 `{"buttons":[{"name":"影子按钮","result":"clicked:shadow"}]}`。

**验证：** 浏览器打开 F5、F6，手动点三个按钮，`#result` 显示对应文字；F5 的跨源 iframe 在开发者工具 frame 列表中显示 localhost 来源。

## K13: F7 表单、F8 懒加载

**文件：** `fixtures/f07_form/`、`fixtures/f08_lazy/`
**依赖：** K9
**步骤：**
1. F7：`<input id=q>` + `<button id=submit>提交</button>`，点击后 `#out` 显示 `你输入了：<值>`（不跳转）。expected：`{"input":"宫保鸡丁 abc","output_prefix":"你输入了："}`。
2. F8：首屏 20 个条目（高度足以超过视口）；监听 scroll，接近底部时 fetch `/api/page?n=2`，追加 10 个链接（文本 `第二页-1`…`第二页-10`）。expected：`{"page2_link_texts":["第二页-1",...,"第二页-10"]}`。

**验证：** 浏览器打开 F7 输入提交看到结果；打开 F8 滚到底看到第二页条目。

## K14: F9 故障页、F11 跳转

**文件：** `fixtures/f09_fault/`、`fixtures/f11_nav/index.html`、`page2.html`、`expected.json`
**依赖：** K9
**步骤：**
1. F9：`<img src="/missing">` 和脚本 `console.error("FIXTURE_ERROR_42")`。expected：`{"failed":[{"url_contains":"/missing","status":404}],"console_contains":"FIXTURE_ERROR_42"}`。
2. F11：`<a id=same href="page2.html">同页跳转</a>`、`<a id=blank href="page2.html?tab=new" target="_blank">新标签页</a>`；page2 标题 "Page2"。expected：`{"same_url_contains":"/f11_nav/page2.html","blank_url_contains":"page2.html?tab=new","origin_url_contains":"/f11_nav/index.html"}`。

**验证：** 浏览器打开 F9 开发者工具看到 404 和 console 报错；F11 两个链接行为正确。

## K15: F10 假搜索 + 假验证码页

**文件：** `fixtures/f10_search/with_reddit.html`、`no_reddit.html`、`reddit.html`、`expected.json`、`fixtures/extra/gate.html`
**依赖：** K9
**步骤：**
1. with_reddit：6 条搜索结果链接，其中一条 `href="reddit.html?from=www.reddit.com"`，文本含 "reddit.com"；其余指向不存在的 example 页面（`/f10_search/r1.html` 等，点到也只是 404）。
2. no_reddit：6 条结果，无 reddit。
3. reddit.html：标题 "reddit.com - fixture"，5 个 `<p class=comment>`，各含一句固定文字（含唯一关键词 KW1…KW5）。
4. expected：`{"comments":["...KW1...",...],"keywords":["KW1","KW2","KW3","KW4","KW5"]}`。
5. extra/gate.html：标题与正文含"人机验证"，并嵌 `<iframe src="about:blank" name="recaptcha">`。

**验证：** 浏览器逐页打开，内容与上述一致。

---

## 阶段 C：CDP 与浏览器

## K16: CDP 连接与命令

**文件：** `src/hands/cdp.py`
**依赖：** K1
**步骤：**
1. 异常 `CDPError(method, code, message)`、`BrowserCrashed`。
2. `CDPConnection`：`connect(ws_url)`（`websockets.connect(max_size=None)`），启动后台 `_reader` 任务；`send(method, params=None, session_id=None, timeout=30)` 自增 id、登记 Future、发送、`asyncio.wait_for`；收到含 id 的消息完成 Future（有 error 则 CDPError）。
3. 事件分发：`on(method, cb, session_id=None)` / `off(...)`；回调可为同步或协程（协程用 `create_task`）；`session_id=None` 的订阅只接收浏览器级事件，传 `"*"` 接收所有会话。
4. `close()`：取消 reader，关闭 websocket。
5. reader 遇到连接关闭时调用 `fail_all(BrowserCrashed("connection closed"))`（除非是主动 close）。

**验证：** 手动启动 `chrome --headless --remote-debugging-port=9333 --user-data-dir=profiles\_tmp`，用 `python -c` 连接其 `/json/version` 的 ws 地址，`send("Browser.getVersion")` 打印出 `HeadlessChrome/156...`。

## K17: CDP 等待事件、批量失败、会话封装

**文件：** `src/hands/cdp.py`
**依赖：** K16
**步骤：**
1. `wait_event(method, predicate=None, session_id="*", timeout)`：注册一次性 Future，命中后注销。
2. `fail_all(exc)`：所有挂起命令 Future 与 wait_event Future 设异常；之后的 `send` 直接抛该异常。
3. `CDPSession(conn, session_id)`：`send/on/off/wait_event` 自动带 session_id。
4. `attach_page(conn, target_id) -> CDPSession`（`Target.attachToTarget{targetId, flatten: true}`）。

**验证：** 沿用 K16 的手动 Chrome：`Target.getTargets` 取 page，attach 后 `Page.enable`、`Page.navigate(about:blank)`，`wait_event("Page.loadEventFired")` 5 秒内返回；随后关闭 Chrome，挂起中的 `wait_event` 以 BrowserCrashed 结束。

## K18: 浏览器启动与关闭

**文件：** `src/hands/browser.py`
**依赖：** K2、K17
**步骤：**
1. `BrowserManager(config)`：`_args()` 生成 `[chrome, --remote-debugging-port=0, --user-data-dir=<profile>, --no-first-run, --no-default-browser-check, --window-size=W,H, (--headless), about:blank]`。
2. `start()`：删除旧 `DevToolsActivePort` → `subprocess.Popen` → 轮询该文件（最多 10 秒，间隔 100ms，这是"等文件出现"的条件轮询）得到端口 → httpx GET `/json/version`（最多 10 秒）→ `CDPConnection.connect(webSocketDebuggerUrl)`；记录 `pid`、`port`、`version_info`。
3. `stop()`：`Browser.close`（3 秒超时）→ 等进程退出 5 秒 → 仍在则 `taskkill /PID <pid> /T /F`；关闭连接。
4. `new_page()` 先返回一个已 attach 的 CDPSession（Page 类完成后在 K22 改为返回 Page）。

**验证：** `python -c` 执行 start→打印 version_info["Browser"]→stop，输出 `HeadlessChrome/156...`，执行后 `Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | ? CommandLine -like '*profiles\ver0*'` 为空。

## K19: 看门狗、重启、切换有界面

**文件：** `src/hands/browser.py`
**依赖：** K18
**步骤：**
1. `crashed = asyncio.Event()`；`_watchdog()` 每 `watchdog_interval_s` 检查 `proc.poll() is not None` 或连接已断；监听 `Inspector.targetCrashed`（任意会话）。触发时记录 `crash_detected_at`，置位 crashed，`conn.fail_all(BrowserCrashed(...))`。
2. `is_alive()`；`restart()`：强制清理旧进程（taskkill）→ 重置 crashed → `start()`。
3. `relaunch(headless: bool)`：`stop()` → 修改 `self.headless` → `start()`。

**验证：** `python -c` 启动后用 `taskkill /PID <pid> /T /F` 杀进程，打印 `crash_detected_at - kill_time` < 5 秒；随后 `restart()` 成功拿到新版本信息；`relaunch(False)` 能看到有界面窗口，再 `relaunch(True)` 后 stop。

## K20: 验收框架 + T01

**文件：** `tests/acceptance/common.py`、`tests/acceptance/t01_launch.py`、`run_tests.py`
**依赖：** K9、K19
**步骤：**
1. `common.py`：`Ctx`（config、fixture server、base/xbase、runs 目录、`headed` 标志、懒创建的共享 BrowserManager）；`load_expected(name)`；`chrome_procs(profile) -> list[pid]`（PowerShell `Get-CimInstance`，按 CommandLine 含 profile 路径筛选）；`python_rss_mb()`（ctypes `GetProcessMemoryInfo`）；`chrome_ws_mb(profile)`（同 CIM 查询 WorkingSetSize 求和）。
2. `run_tests.py`：参数 `--only`、`--rounds`、`--headed`；按编号发现 `tests/acceptance/tNN_*.py`，每个模块 `async def run(ctx) -> tuple[bool, str]`；捕获异常记为失败；最后打印表 `编号 | 通过/失败 | 耗时(s) | 失败原因`，并写入 `runs/<时间戳>-acceptance/acceptance.json`；任一失败则退出码 1。
3. `t01_launch.py`：循环 10 次 start → GET `/json/version` 成功 → stop → `chrome_procs` 为空；任一失败返回原因。

**验证：** `python run_tests.py --only T01` 输出表格一行 `T01 | 通过 | ...`。

---

## 阶段 D：监控与页面

## K21: Monitor

**文件：** `src/hands/monitor.py`
**依赖：** K4、K17
**步骤：**
1. `Monitor()`：内部 `_pending: dict[(session_id, requestId), RequestInfo]`、`_failed`、`_console`、`lifecycle`、`_last_change`（monotonic）。
2. `attach_session(session, main_frame_id=None)`：订阅该会话的 `Network.requestWillBeSent`（type 为 WebSocket/EventSource 的忽略；redirect 时更新 url）、`Network.responseReceived`（status≥400 记 failed）、`Network.loadingFinished`、`Network.loadingFailed`（记 failed，含 errorText，被取消且 status 已记录的不重复）、`Runtime.consoleAPICalled`(type=error)、`Runtime.exceptionThrown`、`Log.entryAdded`(level=error)、`Page.lifecycleEvent`（frameId==main_frame_id 时更新 lifecycle）。每次 pending 增减更新 `_last_change`。
3. `pending_count()`、`quiet_since()`、`snapshot() -> MonitorState`（深拷贝）、`reset_for_navigation()`（清 failed/console/lifecycle，保留 pending）、`detach_session(session_id)`（移除其 pending）。

**验证：** 在 K22 完成后统一验证（见 K22）；本任务先 `python -c "import hands.monitor"` 无报错。

## K22: Page 基础：attach、导航、等待

**文件：** `src/hands/page.py`、`src/hands/browser.py`（new_page 返回 Page）
**依赖：** K21
**步骤：**
1. dataclass `WaitResult(status, elapsed_ms, pending_urls)`；dataclass `FrameSession(session, frame_id, parent: FrameSession | None, owner_offset=(0,0))`。
2. `Page(conn, config)`：`attach(target_id)` → CDPSession；启用 `Page/Network/Runtime/Log/DOM`；`Page.setLifecycleEventsEnabled(true)`；`Page.getFrameTree` 得主 frameId；建 Monitor 并 attach。
3. `wait_settled(after_navigation, timeout)`：若 after_navigation，先等 `Page.loadEventFired`（或 lifecycle 已是 load）；然后循环：`pending_count()==0` 且 `now - quiet_since() >= quiet_ms` 即返回 ok；否则 `await` Monitor 的变化事件（asyncio.Event，带剩余超时，不用固定 sleep；为让"保持 quiet_ms"可判定，等待时长取 `quiet_ms - 已安静时长` 与剩余超时的较小值）；总超时返回 timeout + 当前 pending url 列表。
4. `navigate(url)`：`monitor.reset_for_navigation()` → `Page.navigate` → `wait_settled(True, nav_timeout_s)`；`reload()` 同理用 `Page.reload`。
5. `url()`、`title()`（`Runtime.evaluate` 只读 `location.href`、`document.title`）。
6. `BrowserManager.new_page()`：取第一个 page 类型 target 并返回 attach 好的 Page。

**验证：** 夹具服务运行，脚本 `navigate(BASE/f03_dynamic/index.html)` 返回 ok 且耗时 ≥ 2 秒；`navigate(BASE/f04_slow/index.html)` 用 timeout=5 返回 timeout、耗时 4.5–6 秒、pending_urls 含 `/api/delay?ms=8000`；`navigate(BASE/f09_fault/index.html)` 后 `monitor.snapshot()` failed 含 `/missing` 404，console_errors 含 FIXTURE_ERROR_42。

## K23: 跨源 iframe 会话树

**文件：** `src/hands/page.py`
**依赖：** K22
**步骤：**
1. attach 时对主会话调用 `Target.setAutoAttach{autoAttach: true, waitForDebuggerOnStart: true, flatten: true, filter: [{type: "iframe"}, {type: "page", exclude: true}]}`（Chrome 不接受 filter 时退回不带 filter）。
2. 订阅 `Target.attachedToTarget`（任意会话）：建子 CDPSession → 启用 Network/Runtime/Log/DOM/Page → `monitor.attach_session` → 对子会话也开 setAutoAttach（递归）→ `Runtime.runIfWaitingForDebugger`。记录 `FrameSession(frame_id=targetInfo.targetId, parent=来源会话对应的 FrameSession)`。
3. 订阅 `Target.detachedFromTarget`：移除 FrameSession，`monitor.detach_session`。
4. `sessions()` 返回主会话 + 当前所有子会话。
5. `refresh_offsets()`：对每个子 FrameSession，在父会话 `DOM.getFrameOwner{frameId}` 得 backendNodeId → `DOM.getBoxModel` 取 content 四边形左上角 → 加父的累计偏移，写入 owner_offset。

**验证：** 脚本 `navigate(BASE/f05_iframe/index.html)` 后 `len(page.sessions()) == 2`（主 + 跨源），子会话 frame 的 url 含 `localhost`；`refresh_offsets()` 得到的偏移与主页面中 `#cross` 元素 `getBoundingClientRect` + border + padding 一致（误差 ≤ 1）。

## K24: 新标签页跟随与后退

**文件：** `src/hands/page.py`
**依赖：** K23
**步骤：**
1. attach 时 `Target.setDiscoverTargets(true)`；订阅 `Target.targetCreated`：type=page 且 `openerId == self.target_id` 时放入 `self._new_tabs` 队列并 set 一个 Event。
2. `adopt_new_tab(target_id)`：保存当前 target 为 opener 记录 → 重新 attach 到新 target（新会话、新 Monitor）→ `wait_settled(True)`。
3. `back()`：`Page.getNavigationHistory`；currentIndex>0 时 `navigateToHistoryEntry(entries[i-1].id)` + `wait_settled(True)`；否则若有 opener 记录：`Target.closeTarget` 当前 → 重新 attach opener → 返回 ok；都没有返回 `WaitResult("no_history", ...)`。

**验证：** 在 K30（click）完成后由 K38 验证；本任务先运行 `navigate(BASE/f11_nav/index.html)` → `navigate(page2.html)` → `back()`，url 回到 index.html。

---

## 阶段 E：读取、截图、PageState

## K25: DOM 读取：主 frame 快照与交互判定

**文件：** `src/hands/dom.py`
**依赖：** K22
**步骤：**
1. `DomReader.read(page) -> tuple[list[Element], str]`；本任务只处理主会话。
2. 调 `DOMSnapshot.captureSnapshot{computedStyles: ["display","visibility","opacity"], includeDOMRects: true}`；解析 strings 表、documents[0].nodes（nodeName、backendNodeId、attributes、parentIndex、shadowRootType）、layout（nodeIndex、bounds、styles、text）、scrollOffsetX/Y。
3. 交互判定（暂用标签）：`A` 有 href、`BUTTON`、`INPUT` 且 type≠hidden、`SELECT`、`TEXTAREA`。
4. 对每个候选：有 layout 才有 box；box = bounds − scrollOffset；visible = 有 layout 且 visibility≠hidden 且宽高>0 且 `x+width > 0` 且 `y+height > 0`；in_viewport = 与视口相交；enabled = 无 `disabled` 属性；text = 子孙文本节点拼接（去空白折叠）；href、value（`inputValue`）。
5. 按文档顺序分配 id `e0..`，`_ref = NodeRef(主会话 id, backendNodeId)`。

**验证：** 脚本打开 F1，按 tag 统计得到 button 5、input 3、a 8；打开 F2，正常按钮 visible=True，`left:-9999px` 的 visible=False。

## K26: DOM 读取：AX 角色名称合并

**文件：** `src/hands/dom.py`
**依赖：** K25
**步骤：**
1. 对会话调 `Accessibility.getFullAXTree`（同进程子 frame 用 `Page.getFrameTree` 拿 frameId 逐个调用，带 frameId 参数）；建 `backendDOMNodeId -> (role, name, disabled)` 映射。
2. 合并：有 AX 则用其 role/name；AX 的 disabled=true 时 enabled=False；无 AX（调用失败）时用标签推断 role（a→link、button→button、input→textbox/checkbox/…、select→combobox、textarea→textbox），name = aria-label / text / placeholder / value。
3. 交互判定补充：AX 角色属于 `{button, link, textbox, searchbox, combobox, checkbox, radio, listbox, menuitem, tab, switch, slider, spinbutton}` 的节点也纳入（覆盖 `div role=button` 等）。

**验证：** 打开 F1，按 role 统计 button 5、textbox 3、link 8 与 expected 一致；F2 的 disabled 按钮 enabled=False。

## K27: DOM 读取：同源 iframe、Shadow DOM、跨源 iframe、正文

**文件：** `src/hands/dom.py`
**依赖：** K23、K26
**步骤：**
1. 同源 iframe：快照中 `contentDocumentIndex` 指向的子 document，其元素 box 加上 iframe 元素的 content 偏移（iframe 元素 bounds + border/padding，由 `DOM.getBoxModel` 取 content 左上角，减去主文档滚动）；frame_path 追加该 document 的 frameId。
2. Shadow DOM：节点祖先链中存在 shadowRootType 的标记 `in_shadow=True`（快照已包含 shadow 子树，无需额外调用）。
3. 跨源 iframe：先 `page.refresh_offsets()`；对每个子 FrameSession 同样快照 + AX，box 加 owner_offset，frame_path 为父链，`_ref.session_id` 为子会话 id。
4. 正文：每个 document 的 layout 中带 text 的节点按顺序拼接，多个 frame 依次追加，段落间换行。

**验证：** 打开 F5：读到"同源按钮"和"跨源按钮"，二者 box 与（主页面 iframe 内容框偏移 + 子页面内按钮 getBoundingClientRect）误差 ≤ 1；打开 F6：读到"影子按钮"且 in_shadow=True；F10 reddit 页正文含 KW1–KW5。

## K28: 截图与标注

**文件：** `src/hands/screenshot.py`
**依赖：** K22、K4
**步骤：**
1. `capture(page, full_page=False) -> bytes`：可视区直接 `Page.captureScreenshot{format: png}`；整页先 `Page.getLayoutMetrics` 取 `cssContentSize`，再带 `captureBeyondViewport: true` 与 clip（scale 1）。
2. `device_pixel_ratio(page)`：`Runtime.evaluate("window.devicePixelRatio")`。
3. `annotate(png, elements, dpr) -> bytes`：PIL 打开，每个 visible 元素按 `css_to_image` 画矩形（可见=绿色、不可用=灰色）和 id 标签。

**验证：** 打开 F1，保存可视区截图、整页截图、标注图到 `runs/_t28/`；整页图高度大于可视区；人工打开标注图，框与按钮/输入框/链接对齐。

## K29: PageState 组装

**文件：** `src/hands/pagestate.py`
**依赖：** K27、K28
**步骤：**
1. `async build_page_state(page, save_dir=None, shot_name="screenshot.png") -> PageState`：并行无关部分——DomReader.read、url/title、dpr、viewport（`Page.getLayoutMetrics.cssLayoutViewport`）、monitor.snapshot；save_dir 不为空时截图写入并在 screenshot 字段记相对路径。
2. `PageState.fingerprint() -> tuple`：(url, sha1(元素 role/name/text/value/visible 序列 + body_text))。

**验证：** 打开 F7，`build_page_state(page, 'runs/_t29')` 后 `to_json` 写出，文件内容含 elements、monitor、screenshot 路径，截图文件存在。

---

## 阶段 F：动作

## K30: 动作框架与点击

**文件：** `src/hands/actions.py`
**依赖：** K29、K24
**步骤：**
1. dataclass `ActionResult(ok, effective, changes, wait, data=None, error=None)`。
2. `ActionExecutor(page)`；内部 `_snapshot_fp()`：PageState.fingerprint + 焦点节点（主会话 `DOM.getFocus` 失败则 None）。
3. `_locate(el) -> (x, y)`：按 `el._ref.session_id` 找 FrameSession；先对其祖先 iframe 的 owner 节点在父会话 `DOM.scrollIntoViewIfNeeded`，再对目标 `DOM.scrollIntoViewIfNeeded{backendNodeId}`；`page.refresh_offsets()`；目标会话 `DOM.getContentQuads` → `quad_to_box` → 加 owner_offset → center。
4. `click(el)`：前指纹 → `_locate` → 主会话 `Input.dispatchMouseEvent` mouseMoved / mousePressed(button left, clickCount 1) / mouseReleased → 若动作后等待窗口内有新标签页事件则 `adopt_new_tab`，changes 加 new_tab → `wait_settled(after_navigation=url 变化)` → 后指纹 → effective/changes。
5. `_run(fn)` 包装：异常（CDPError、元素已消失）转为 `ok=False, error=...`；BrowserCrashed 不吞，向上抛。

**验证：** 打开 F7，点击 `#submit`（输入框为空），ActionResult ok=True 且 effective=True（`#out` 文字变化）。

## K31: 输入与按键

**文件：** `src/hands/actions.py`
**依赖：** K30
**步骤：**
1. `type(el, text)`：先 `click(el)` 聚焦 → `Input.insertText{text}` → 等网络安静 → 读回该元素 value（重新快照）比对，不一致则 `ok=False, error="value mismatch"`。
2. `press(key)`：内置映射 Enter/Tab/Escape/Backspace/ArrowDown/ArrowUp/PageDown/End（key、code、windowsVirtualKeyCode、text）；发 keyDown（Enter 带 text "\r"）/ keyUp；之后与 click 相同的新标签页/等待/指纹流程。

**验证：** 打开 F7，`type(#q, "宫保鸡丁 abc")` → `click(#submit)` → body_text 含 `你输入了：宫保鸡丁 abc`；再用 `press("Enter")` 在 `#q` 上提交同样得到结果（F7 表单已监听 Enter）。

## K32: 滚动与后退

**文件：** `src/hands/actions.py`
**依赖：** K30
**步骤：**
1. `scroll(direction=None, pixels=None, to_bottom=False)`：在视口中心发 `Input.dispatchMouseEvent{type: mouseWheel, deltaY: ±pixels}`；to_bottom 时循环滚动，每次 deltaY = 视口高度，直到 `Page.getLayoutMetrics` 的滚动位置不再增加且网络安静（最多 50 次）。
2. `back()`：调 `page.back()`，返回带前后指纹比较的 ActionResult。

**验证：** 打开 F8，`scroll(to_bottom=True)` 后重新读元素，链接文本含 `第二页-1`…`第二页-10`。

## K33: 读取工具

**文件：** `src/hands/actions.py`
**依赖：** K29
**步骤：**
1. `read_text(element=None, contains=None)`：读最新 PageState；element 给定则取其 text，否则取 body_text；contains 给定时按行筛选含该词的行；结果放 `data`（str）。不改变页面，effective=False。
2. `read_links(href_contains=None, href_excludes=None)`：取 visible 的 role=link 元素，按条件过滤，`data = [{"title": name or text, "href": href}]`。

**验证：** 打开 F10 reddit 页，`read_text(contains="KW")` 返回 5 行；打开 with_reddit，`read_links(href_contains="reddit")` 返回 1 条。

---

## 阶段 G：感知与动作验收（T03–T12）

## K34: 验收 T03、T04、T05

**文件：** `tests/acceptance/t03_counts.py`、`t04_visibility.py`、`t05_boxes.py`
**依赖：** K20、K29
**步骤：**
1. T03：打开 F1，按 role 统计 visible 元素，与 expected.counts 全等。
2. T04：打开 F2，对 expected.elements 每个 id（用 Runtime.evaluate 只读取得 `document.getElementById(id)` 的 backendNodeId 映射，或按元素 name 匹配）校验 visible、enabled；display:none 的元素"不在列表"也算对。
3. T05：打开 F1，对每个元素用 `Runtime.evaluate` 只读获取 `getBoundingClientRect()`（按 id 查），与 box 四值误差 ≤ 1；生成标注图到本次 runs 目录，路径写进返回原因字段便于人工核对。

**验证：** `python run_tests.py --only T03,T04,T05` 三行均为通过。

## K35: 验收 T06、T07

**文件：** `tests/acceptance/t06_dynamic.py`、`t07_timeout.py`
**依赖：** K20、K29
**步骤：**
1. T06：打开 F3，navigate 返回 ok 后立即读元素（不再额外等待），`#list` 内链接数 == 6；同时断言 navigate 耗时 ≥ 2 秒（说明等的是网络而非固定时长）。另在 T06 中对源码执行静态检查：`src/hands` 下 page.py/actions.py 不出现 `asyncio.sleep(` 的固定常数调用（`grep` 结果为空或仅限 browser.py 中等待端口文件的轮询）。
2. T07：配置 nav_timeout_s=5 打开 F4，结果 status=timeout，耗时 4–6 秒，pending_urls 含 `/api/delay?ms=8000`。

**验证：** `python run_tests.py --only T06,T07` 均通过。

## K36: 验收 T08（iframe / Shadow DOM）

**文件：** `tests/acceptance/t08_frames.py`
**依赖：** K20、K30
**步骤：**
1. 打开 F5：确认 `page.sessions()` 中有 url 含 localhost 的子会话（跨源独立进程证据）；找到"同源按钮""跨源按钮"，各自 box 与对应 frame 内 `getBoundingClientRect` + iframe 内容框偏移误差 ≤ 1；依次点击，读主页面 `#result` 文本与 expected 一致。
2. 打开 F6：找到"影子按钮"，in_shadow=True，点击后 `#result` == `clicked:shadow`。

**验证：** `python run_tests.py --only T08` 通过。

## K37: 验收 T09、T10

**文件：** `tests/acceptance/t09_form.py`、`t10_lazy.py`
**依赖：** K20、K31、K32
**步骤：**
1. T09：打开 F7，type 输入框 expected.input → click 提交 → body_text 含 `output_prefix + input`。
2. T10：打开 F8，记录初始无"第二页-1" → scroll(to_bottom) → 重新读元素，包含全部 page2_link_texts。

**验证：** `python run_tests.py --only T09,T10` 均通过。

## K38: 验收 T11、T12

**文件：** `tests/acceptance/t11_fault.py`、`t12_nav.py`
**依赖：** K20、K30、K32
**步骤：**
1. T11：打开 F9，monitor.failed 中有 url 含 `/missing` 且 status 404；console_errors 某条 text 含 `FIXTURE_ERROR_42`。
2. T12：打开 F11，click"同页跳转"→ url 含 same_url_contains → back → url 含 origin_url_contains；click"新标签页"→ changes 含 new_tab 且 url 含 blank_url_contains → back → url 含 origin_url_contains。

**验证：** `python run_tests.py --only T11,T12` 均通过。

---

## 阶段 H：控制层、报告与用例验收

## K39: 运行记录与报告

**文件：** `src/hands/recorder.py`
**依赖：** K29、K6
**步骤：**
1. dataclass `StepRecord`（字段同 plan，`to_dict()`）。
2. `RunRecorder(runs_dir, label="")`：创建 `runs/<YYYYmmdd-HHMMSS>-<4位随机>[-label]/`；`case_dir(name)` 创建子目录。
3. `save_step(case, step, page_state, png)`：写 `step_###/pagestate.json`、`screenshot.png`、`decision.json`（raw + 解析结果 + retries）、`action.json`。
4. `save_tree(case, tree)` → `tree_events.json`；`save_case_result(case, result)` → `result.json`。
5. `write_report(results)`：`report.json`；`report.html`（汇总表：用例、通过/失败、原因、崩溃次数、耗时；每用例展开每步：工具、参数、effective、耗时、截图链接）。

**验证：** `python -c` 构造 2 个假 StepRecord 写入并 write_report，打开 report.html 能看到表和截图链接；连续执行两次生成两个不同目录。

## K40: 用例加载与主循环

**文件：** `src/hands/runner.py`
**依赖：** K7、K33、K39、K8
**步骤：**
1. dataclass `Expect`、`CaseSpec`、`CaseResult`；`load_case(path)` 解析 YAML（start_url 中 `${BASE}` / `${XBASE}` 由调用方传入的 vars 替换），rules 转 Rule。
2. `CaseRunner(browser, config, recorder, decider_factory=RuleDecider.from_case, on_step=None, input_fn=input)`。
3. `run_case(case)`（无崩溃处理版本）：navigate(start_url) → 新 TaskTree → 循环：CHECK（build_page_state 存入步骤目录；captcha.detect 命中则调用 handoff 后 continue）→ ANALYZE（decide_with_retry；None 则 tree.fail("invalid decision")）→ EXECUTE（tree 工具直接调用 tree；动作/读取工具：按 element id 从 state 取 Element，调用 ActionExecutor）→ VERIFY（tree.record_step 摘要、tree.tick_step、recorder.save_step）→ on_step(i)；直到 tree.finished 或超时（超时 → fail("timeout")）。
4. `_judge(case, tree, page, clicks)`：按 Expect 判定 root_status、summary_contains（在 root summary 与所有 child summary 中查找）、final_url_contains、tree_shape、no_clicks；生成 CaseResult。
5. `run_cases(paths)`：逐条执行，单条异常记失败继续；最后 write_report。

**验证：** 先写临时用例（打开 F7，规则：输入 → 点击 → read_text → complete("{{last_read}}")），`python -c` 运行 run_case，结果 passed=True，runs 目录下有每步文件。

## K41: 崩溃重跑、域名间隔、人工介入

**文件：** `src/hands/runner.py`、`src/hands/captcha.py`
**依赖：** K40、K19
**步骤：**
1. `run_case` 外包循环：捕获 BrowserCrashed → crashes += 1 → 超过 max_crash_reruns 则失败（reason="crashed N times"）→ `browser.restart()`、重新 new_page → 从头重跑；CaseResult.crashes 记录次数。
2. 域名间隔：navigate 前查 `min_interval_s` 中与目标域名后缀匹配的项，距上次访问不足则 `asyncio.sleep(剩余秒数)`（这是礼貌间隔，不是页面等待）。
3. `captcha.handoff(browser, page_holder, url, save_dir, input_fn)`：截图保存 → `browser.relaunch(headless=False)` → new_page.navigate(url) → 打印提示（截图路径、"请在弹出的浏览器窗口中完成验证，完成后按回车"）→ `input_fn()` → `browser.relaunch(headless=True)` → new_page.navigate(url) → 返回新 page。
4. 单元测试 `test_captcha.py` 补充：用假 browser/page 验证 handoff 调用顺序（relaunch False → input → relaunch True）。

**验证：** `pytest tests/unit -q` 全部通过；临时脚本运行一条打开 `extra/gate.html` 的用例，input_fn 注入为立即返回，日志显示进入人工介入、重启两次、继续执行。

## K42: CLI

**文件：** `src/hands/__main__.py`
**依赖：** K41
**步骤：**
1. `python -m hands run <case.yaml...> [--headed] [--config config.yaml]`：load_config → 若用例 URL 含 `${BASE}` 则自动启动夹具服务 → BrowserManager.start → CaseRunner.run_cases → 打印每条结果与 report.html 路径 → stop。
2. 退出码：全部通过 0，否则 1。

**验证：** `python -m hands run <K40 的临时用例>` 打印通过与报告路径。

## K43: T13 用例与验收

**文件：** `cases/t13_reddit_yes.yaml`、`cases/t13_reddit_no.yaml`、`tests/acceptance/t13_reddit.py`
**依赖：** K42
**步骤：**
1. yes 用例：start_url `${BASE}/f10_search/with_reddit.html`；limits max_depth 3、max_steps 10；规则顺序：
   - depth 0 且存在 href 含 reddit 的 link 且 no_child_with_goal "读取评论" → create_child(goal "读取评论", done_when "读到全部评论预览")
   - goal_is "读取评论" 且 url 不含 reddit 且存在 reddit link → click {{match}}
   - goal_is "读取评论" 且 url_contains "reddit" 且 read_count 0 → read_text(contains: "KW")（以 fixture 的 comment 行为准）
   - goal_is "读取评论" 且 read_count ≥ 1 → complete("{{last_read}}")
   - depth 0 且 has_child_summary → complete("子任务完成")
   - depth 0 且 no_element(reddit link) → complete("未出现 reddit 链接")
   expect：root_status done、summary_contains KW1–KW5、tree_shape `["root","root/读取评论"]`、final_url_contains reddit。
2. no 用例：同规则，start_url no_reddit.html；expect：root_status done、summary_contains "未出现"、no_clicks true、tree_shape `["root"]`。
3. 验收脚本：跑两条用例，检查 passed，并读取 yes 用例 tree_events.json 确认事件序列 create(root)→create(child)→complete(child)→complete(root)。

**验证：** `python run_tests.py --only T13` 通过。

## K44: T14、T15 验收

**文件：** `cases/t14_unreachable.yaml`、`tests/acceptance/t14_step_limit.py`、`tests/acceptance/t15_context.py`
**依赖：** K43
**步骤：**
1. t14 用例：打开 F1，max_steps 5，唯一规则 `scroll(direction down, pixels 100)`；expect root_status failed。验收：运行有总时限 60 秒（`asyncio.wait_for`），结果 failed 且 tree_events 末尾含 force_fail(step_limit)。
2. T15：运行 t13_reddit_yes，在 on_step 钩子里于子任务 complete 之后的第一步捕获 `tree.view()` 与 root 节点：root.child_summaries 含子摘要；子节点 context 为 None；序列化 root 的 TaskView 不含 `elements`/`body_text` 等 PageState 字段。

**验证：** `python run_tests.py --only T14,T15` 均通过。

## K45: T16 报告验收

**文件：** `cases/t16_report.yaml`、`tests/acceptance/t16_report.py`
**依赖：** K43
**步骤：**
1. t16 用例：F7 表单输入 → 点击 → read_text → complete，共 ≥ 4 步。
2. 验收：运行 t16 与 t13_reddit_yes；每个用例目录有 result.json（含 passed、reason、duration_ms）；每个 step 目录都有 pagestate.json（可被 json 解析且含 elements）、decision.json、action.json、screenshot.png（PIL 可打开）；StepRecord 含 durations_ms；report.json 与 report.html 存在且列出两条用例；再跑一次确认 runs 下生成新目录、旧目录文件未变（AC20）。

**验证：** `python run_tests.py --only T16` 通过。

## K46: T02 崩溃恢复验收

**文件：** `cases/t02_crash.yaml`、`tests/acceptance/t02_crash.py`
**依赖：** K41、K45
**步骤：**
1. t02 用例：与 t16 相同流程。
2. 验收：on_step 钩子在第一次运行的第 2 步后执行 `taskkill /PID <browser.pid> /T /F` 并记 kill 时间（只触发一次）；结束后检查 `browser.crash_detected_at - kill_time < 5`、CaseResult.passed=True、crashes == 1、report.json 中该用例 crashes == 1。

**验证：** `python run_tests.py --only T02` 通过。

## K47: T17 稳定性

**文件：** `tests/acceptance/t17_stability.py`
**依赖：** K34–K38、K43
**步骤：**
1. 共享一个浏览器实例，循环 `--rounds`（默认 50）轮依次执行 T03–T13 的 run 函数（复用模块，传入共享 ctx）；任一失败记录轮次与原因。
2. 第 5 轮与第 50 轮结束后各测 python_rss_mb 与 chrome_ws_mb；要求两者增长都不超过 20%。
3. 返回原因中写入通过率与内存数字。

**验证：** `python run_tests.py --only T17 --rounds 3` 通过（快速检查）；正式 50 轮在验收阶段跑。

## K48: T20 Bing 用例

**文件：** `cases/t20_bing.yaml`
**依赖：** K42
**步骤：**
1. start_url `https://www.bing.com/`；inputs.query 写一个普通词（如 `python asyncio`）；规则：存在 role searchbox/combobox/textbox 且 url 不含 `search?` → type({{match}}, {{inputs.query}})；last_tool type → press Enter；url_contains "search?" 且 read_count 0 → read_links(href_excludes: "bing.com")；read_count ≥ 1 → complete("{{last_read}}")。
2. expect：root_status done、final_url_contains "search"。
3. 用 `python -m hands run cases/t20_bing.yaml` 跑一次，人工打开报告核对标题和链接与 Bing 页面一致；若遇验证码，按提示在有界面窗口处理后回车。

**验证：** 命令输出通过；report 中 read_links 结果为非空标题+链接列表，人工抽查 5 条正确。

## K49: 一键验收总跑

**文件：** 无新文件
**依赖：** K1–K48
**步骤：**
1. `pytest -q`（单元测试）。
2. `python run_tests.py`（T01–T17，T17 默认 50 轮）。
3. 失败项回到对应任务（K 编号）修复后重跑。

**验证：** 单元测试全部通过；验收表 T01–T17 全部"通过"，退出码 0。

---

## 执行顺序

```
K1 → K2 ─────────────┐
K1 → K3 → K4 → K5 ─┐ │
K1 → K6 ───────────┴→ K7
K4 → K8
K1 → K9 → K10…K15（可并行）
K1 → K16 → K17 → K18(需 K2) → K19 → K20(需 K9)
K17 + K4 → K21 → K22 → K23 → K24
K22 → K25 → K26 → K27(需 K23) ; K22 → K28 ; K27+K28 → K29
K29 + K24 → K30 → K31, K32 ; K29 → K33
K20 + 感知/动作 → K34, K35, K36, K37, K38
K29 + K6 → K39 ; K7+K8+K33+K39 → K40 → K41 → K42
K42 → K43 → K44, K45 → K46 ; K34–K38+K43 → K47 ; K42 → K48
全部 → K49
```
