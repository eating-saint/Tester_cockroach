# 控制面板（panel）Tasks

日期：2026-11-12　状态：待用户审批　依据：`docs/panel/spec.md`、`docs/panel/plan.md`

编号：开发任务 **K1–K41**；需要用户亲手做的 **U1–U4**；验收标准仍用 spec 的 AC 编号。
环境标注：**[云]** 在云端 Linux + Chrome 153 开发和验证；**[本机]** 必须在用户的 Windows 电脑上验证。
每个阶段结束时跑 ver0 回归：`pytest -q` + `python run_tests.py --rounds 3`（阶段 I 跑满 50 轮）。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 新建 | `tools/bench_screencast.py` | 推流基准测试 |
| 新建 | `fixtures/panel_bench/index.html` | 持续动画页（基准测试和 AC5 用） |
| 修改 | `pyproject.toml` | optional 依赖 panel / build；包含 panel 包 |
| 修改 | `config.yaml` | `humanize.mouse.source/device/library_path` |
| 新建 | `src/hands/trajlib.py` | 样本结构、库读写、挑样本、相似变换 |
| 新建 | `src/hands/status.py` | 进程内实时状态 LiveStatus |
| 修改 | `src/hands/humanize.py` | 轨迹来源切换、Move 新字段、think 状态 |
| 修改 | `src/hands/page.py` | 等待状态、换页通知 |
| 修改 | `src/hands/actions.py` | before_action 钩子、动作完成通知 |
| 修改 | `src/hands/runner.py` | should_stop、before_action、handoff_fn、humanizer_factory、阶段通知、验证码签名、人工介入时间 |
| 修改 | `src/hands/captcha.py` | 补真实验证框标记、signature() |
| 修改 | `src/hands/config.py` | DATA_ROOT、默认标记 |
| 修改 | `src/hands/recorder.py` | 报告写人工介入时间 |
| 新建 | `src/panel/__init__.py` `__main__.py` | 包、开发入口 |
| 新建 | `src/panel/server.py` | aiohttp 路由、鉴权、静态、WS |
| 新建 | `src/panel/service.py` | PanelService |
| 新建 | `src/panel/jobs.py` | JobManager 与五种任务 |
| 新建 | `src/panel/preview.py` | 推流 |
| 新建 | `src/panel/inputs.py` | 手动输入转发 |
| 新建 | `src/panel/settings.py` | 设置表、校验、生效、写回 |
| 新建 | `src/panel/samples.py` | 样本库、注入脚本 |
| 新建 | `src/panel/history.py` `profile.py` | 运行历史、用户目录 |
| 新建 | `src/panel/app.py` | Windows 窗口入口、单实例 |
| 新建 | `src/panel/static/index.html` `style.css` `app.js` | 主界面 |
| 新建 | `src/panel/static/collect.html` `collect.js` | 本地采集页 |
| 新建 | `fixtures/f13_turnstile/pass.html` `interactive.html` | Cloudflare 官方测试密钥验证框 |
| 新建 | `fixtures/f14_cookie/index.html` | 写入 / 读取 Cookie |
| 新建 | `cases/extra_turnstile.yaml` `cases/panel_stop.yaml` | 真实验证框用例、可中途停止的长用例 |
| 新建 | `packaging/panel.spec` | PyInstaller |
| 新建 | `tests/unit/test_trajlib.py` `test_samples.py` `test_status.py` `test_auth.py` `test_settings.py` | 单元测试 |
| 修改 | `tests/unit/test_captcha.py` `test_humanize.py` | 补用例 |
| 新建 | `tests/panel/common.py` `p01_*.py`… | 面板验收 |
| 新建 | `run_panel_tests.py` | 面板验收表 |

---

## 阶段 0：用户配合（和 K1 起的开发同时进行）

### U1：AC0 回归与提交 [本机]
**步骤：** 在项目根目录的 PowerShell 里依次运行 `pytest -q`、`python run_tests.py`；全部通过后 `git add -A`、`git commit -m "humanize: mouse trajectory + optional disturbances"`、`git push`。
**验证：** pytest 全过；验收表 T01–T17、T21 全部"通过"；`git log -1` 显示这次提交。失败项按 mew-debug 记进 `docs/ver0/debug.md`。

### U2：推流基准测试 [本机]
**依赖：** K1
**步骤：** `python tools/bench_screencast.py`
**验证：** 输出三档画质的帧率和延迟中位数，并写入 `runs/bench_screencast.json`；把最高帧率填进 spec N5（标准 = 该值 × 90%）。

---

## 阶段 A：轨迹与采集先行

### K1：推流基准测试脚本 [云]
**文件：** `fixtures/panel_bench/index.html`、`tools/bench_screencast.py`
**依赖：** 无
**步骤：**
1. 夹具页：一个色块用 `requestAnimationFrame` 持续做圆周运动，页面左上角显示帧计数。
2. 脚本：用 hands 的 `load_config` + `BrowserManager` 无界面启动 → `new_page` → 打开夹具（自带启动夹具服务）→ 对三档参数（质量 70 / 最大宽 1280，60 / 960，50 / 640）各 `Page.startScreencast` 10 秒，每帧 ack，统计帧数和"收到时刻 − metadata.timestamp" → 打印表格并写 JSON。
**验证：** 云端运行，三档都输出帧率（预期约 60）和延迟中位数（预期 < 20 ms）；结束后无残留 Chrome。

### K2：依赖与包配置 [云]
**文件：** `pyproject.toml`
**依赖：** 无
**步骤：** `[project.optional-dependencies]` 增加 `panel = ["aiohttp", "pywebview"]`、`build = ["pyinstaller"]`；`packages.find` 保持 `where=["src"]`（自动包含 `panel`）。核心 `dependencies` 不变。
**验证：** `pip install -e .[panel,test]` 成功；在未装 aiohttp 的环境里 `python -c "import hands.runner"` 仍成功。

### K3：trajlib 数据结构与读写 [云]
**文件：** `src/hands/trajlib.py`、`tests/unit/test_trajlib.py`
**依赖：** 无
**步骤：**
1. `Sample` 数据类（字段见 plan 3.2）；`Sample.from_raw(dict)` 计算 `direction`（复用 `humanize.direction_of`）和 `id`（对 device、start、end、points 的规范 JSON 取 sha1 前 12 位）。
2. `Library`：`load(path)`（文件不存在返回空库；版本号不是 1 抛 `ValueError`）、`save(path)`（UTF-8、`ensure_ascii=False`、indent=1，先写临时文件再替换）。
**验证：** 单元测试：读写往返一致；同内容两次 `from_raw` 得到同一 id；八个方向的样本 direction 正确。

### K4：trajlib 挑样本与变换 [云]
**文件：** `src/hands/trajlib.py`、`tests/unit/test_trajlib.py`
**依赖：** K3
**步骤：**
1. `pick(direction, device, rng)`：筛选同方向、同设备（`mixed` 不限设备）的样本，按 id 排序后 `rng.choice`；没有返回 None。
2. `transform(sample, start, landing, rng, min_ms, max_ms)`：样本向量 `end − start` 映射到 `landing − start` 的相似变换（缩放 + 旋转 + 平移）；时间系数 = clamp(样本时长 × 新距离 / 样本距离, min, max) / 样本时长；最后一点强制等于 landing；第一点强制等于 start。
**验证：** 单元测试：500 次随机起终点，首点误差 0、末点误差 0；时长都在 180–1100 ms；同一 rng 种子两次结果完全一致；时间戳单调不减。

### K5：humanize 接入轨迹库 [云]
**文件：** `src/hands/humanize.py`、`config.yaml`、`tests/unit/test_humanize.py`
**依赖：** K4
**步骤：**
1. `MouseConfig` 加 `source="template"`、`device="mouse"`、`library_path="trajlib/samples.json"`；`from_dict` 读取这三项。
2. `Move` 加 `source`、`sample_id`、`fallback`，写进 `to_dict`。
3. `Humanizer.__init__(cfg, seed, library=None)`；`source=="library"` 且 `library is None` 时按 `library_path`（相对 DATA_ROOT，K14 之前相对 PROJECT_ROOT）加载。
4. 新增 `plan_library_move(start, target, rng, cfg, viewport, library)`：先 `pick_landing`，再按方向 `pick`；有样本 → `transform`，组装 Move（template 字段写 `"library"`）；无样本 → 调 `plan_move`，`fallback=True`。
5. `move_to` 按 `cfg.mouse.source` 分流。
6. `config.yaml` 的 `humanize.mouse` 加三行注释说明，默认 `source: template`。
**验证：** 单元测试新增：库为空时 fallback=True；有样本时 source=library、sample_id 非空、末点等于落点；同种子同结果。`python run_tests.py --only T21` 通过（默认 template，行为不变）。

### K6：样本库服务端 SampleStore [云]
**文件：** `src/panel/__init__.py`、`src/panel/samples.py`、`tests/unit/test_samples.py`
**依赖：** K3
**步骤：**
1. `SampleStore(path)`：内部持有 `Library`。
2. `add(raw)`：复核作废规则，按顺序返回第一个原因：`miss`（末点不在目标框内）、`left_area`（`raw.left_area` 为真）、`too_slow`（末点时间 > 5000 ms）、`too_few`（点数 < 8）；通过则生成 Sample、id 已存在时返回 `duplicate`，否则写库。
3. `delete(id)`、`list(filter)`、`grid()`（`{device: {0..7: {"local": n, "real": n}}}`）、`export() -> bytes`、`import_(bytes, mode)`（`merge` 跳过重复 id，`replace` 整体替换；版本不符返回错误）。
**验证：** 单元测试覆盖四种作废原因、去重、导出后导入（merge 两次条数不变、replace 后与文件一致）、grid 计数。

### K7：面板后台骨架与鉴权 [云]
**文件：** `src/panel/server.py`、`src/panel/__main__.py`、`tests/unit/test_auth.py`
**依赖：** K2、K6
**步骤：**
1. `create_app(service, token, port)`：中间件对 `/api/*`、`/ws` 校验：`Host` 必须是 `127.0.0.1:端口`；有 `Origin` 时必须等于 `http://127.0.0.1:端口`；`X-Panel-Token` 头（WS 用查询参数）等于令牌。失败返回 403。
2. `/`、`/collect` 返回静态页，把 `{{TOKEN}}` 替换成令牌；所有响应加 CSP `default-src 'self'; img-src 'self' blob: data:; connect-src 'self' ws://127.0.0.1:端口`。
3. 样本路由：`GET/POST /api/samples`、`DELETE /api/samples/{id}`、`GET /api/samples/export`、`POST /api/samples/import?mode=`、`GET /api/samples/grid`。
4. `__main__`：`python -m panel [--port 0] [--no-window]`：只绑 127.0.0.1，打印带令牌的地址；此阶段 service 只含 SampleStore。
**验证：** `test_auth.py`（aiohttp 测试客户端）：无令牌 403、错令牌 403、`Origin: http://evil.example` 403、`Host: 192.168.1.2:端口` 403、正确时 200；`python -m panel --no-window` 启动后用 curl 带令牌取 `/api/samples/grid` 返回 JSON。

### K8：本地采集页——布局与出题 [云]
**文件：** `src/panel/static/collect.html`、`collect.js`
**依赖：** K7
**步骤：**
1. 顶部：设备选择（鼠标 / 触控板，未选不能开始）、"开始一组"、进度格（8 方向 × 5，按设备分两张，格内显示"本地/真实"条数）。
2. 采集区：用组种子生成仿真实网站布局——顶部导航栏（5–7 个链接）、搜索框 + 按钮、左侧分类列表、3–8 张商品卡片（含"加入购物车"按钮、收藏小图标 16–24 px）、分页按钮；位置、数量、尺寸随种子变化，可点控件尺寸覆盖 16–80 px。
3. 出题：按方向轮转（跳过已满的格子）；在布局里找"起点控件"和"目标控件"，使两者中心连线方向属于该方向、距离 150–600 px；找不到时换种子重排布局。起点控件标绿框"①先点这里"，目标控件标橙框"②再点这里"。
**验证：** 在云端用 Chrome 打开 `/collect`，执行页面里的自检函数 `__selfTest(200)`：生成 200 道题，八个方向都出现，所有题距离在 150–600、目标尺寸在 16–80 范围内（结果打印到 console，由脚本读取断言）。

### K9：本地采集页——记录、校验、提交 [云]
**文件：** `collect.js`
**依赖：** K8
**步骤：**
1. 点中起点控件后开始记录：`pointermove`（用 `getCoalescedEvents()` 展开）记录 `[x, y, t]`（CSS 像素、`performance.now()` 相对起点点击的毫秒）；`pointerleave` 采集区时标记 `left_area`。
2. 点击（`pointerdown` 位置为落点）后结束：组装 `{device, source:"local", start, end, target, points, dpr: devicePixelRatio, left_area}`；前端先按同样四条规则预校验，不通过显示原因并重出这道题；通过则 POST，显示服务端结果，刷新进度格。
**验证：** 云端用 CDP `Input.dispatchMouseEvent` 模拟一次正常采集 → 样本入库、格子 +1；再分别模拟"点偏""移出采集区""等 6 秒""只发 3 个点"→ 各显示对应原因、库条数不变。

### K10：本地采集页——回看、删除、补采、导入导出 [云]
**文件：** `collect.js`、`collect.html`
**依赖：** K9
**步骤：**
1. "样本"标签：列表（设备、来源、方向、时长、点数、时间），点一条在画布上画出轨迹（起点绿圈、落点红点、目标框蓝框），"回放"按时间戳动画重放。
2. 删除单条（确认后调 DELETE）；"补采"只对未满的格子出题。
3. "导出"下载 JSON；"导入"选文件 + 选合并 / 替换。
**验证：** 云端脚本：采 3 条 → 删 1 条（格子减 1）→ 导出 → 替换导入一个空库 → 再合并导入导出文件，条数回到 2；回放动画在 1 秒内画完一条 400 ms 的轨迹（检查画布非空）。

### U3：开始采集 [本机]
**依赖：** K10 写回本机
**步骤：** `pip install -e .[panel]` → `python -m panel --no-window` → 用 Chrome 打开打印出的地址 → 进入 `/collect`，鼠标、触控板各采满 40 条（AC17 的采集部分）。
**验证：** 两张进度格全满；`data`（开发时为项目根）下 `trajlib/samples.json` 有 80 条。

---

## 阶段 B：hands 其余增量

### K11：LiveStatus [云]
**文件：** `src/hands/status.py`、`tests/unit/test_status.py`
**依赖：** 无
**步骤：** 按 plan 3.1 实现 `WaitInfo`、`LiveStatus`（`subscribe` 返回取消函数；回调异常被吞掉并不影响主流程）、单例 `STATUS`。
**验证：** 单元测试：订阅后 set_wait / clear_wait / set_phase / page_changed / action_done 各触发一次回调；取消后不再触发；回调抛异常不向外传。

### K12：page / actions / humanize 状态接入 [云]
**文件：** `src/hands/page.py`、`src/hands/actions.py`、`src/hands/humanize.py`
**依赖：** K11
**步骤：**
1. `wait_settled`：等 load 时 `STATUS.set_wait("load", timeout)`，进入网络安静循环时 `set_wait("network_quiet", timeout)`，返回前 `clear_wait()`（用 try/finally）。
2. `Page.attach` 末尾 `STATUS.page_changed()`。
3. `ActionExecutor.__init__(..., before_action=None)`；`_run` 开头调用 `before_action()`（可为协程）；返回前 `STATUS.action_done({...})`（tool 名由调用方法传入 `_run(fn, tool=...)`）。
4. `Humanizer.think` 停顿时 `set_wait("think")`，结束 `clear_wait()`。
**验证：** `pytest -q` 全过；`python run_tests.py --rounds 3` 全过（T06 的"禁止 sleep"扫描不受影响）。

### K13：runner、captcha、recorder 增量 [云]
**文件：** `src/hands/runner.py`、`src/hands/captcha.py`、`src/hands/recorder.py`、`tests/unit/test_captcha.py`
**依赖：** K12
**步骤：**
1. `CaseRunner.__init__` 新增 `should_stop=None, before_action=None, handoff_fn=None, humanizer_factory=None`（handoff_fn 为 None 时用 `captcha.handoff`）。
2. `_attempt`：每步开头 `should_stop()` 为真 → `tree.fail("user_stopped")` 并跳出；`STATUS.set_phase` 在 CHECK / ANALYZE / EXECUTE 切换，结束时置 None；ActionExecutor 传 `before_action`；`humanizer_factory` 非空时用它创建 Humanizer。
3. 验证码：`captcha.signature(state, hit)` = `(网址去掉 # 之后的部分, hit)`；runner 维护 `_handled: set`，命中且签名已在集合里则视为未命中；调用 handoff 前后记录时间，追加到 `self._handoffs[case]`，`CaseResult` 增加 `human_handoffs`。
4. 默认标记补 `challenges.cloudflare.com`（config.py 和 config.yaml 同步）。
5. recorder：report.json 的用例结果包含 `human_handoffs`；report.html 在用例行下显示"人工介入：起止时间、命中标记"。
6. `CaseResult.reason` 为 `user_stopped` 时 `passed=False`。
**验证：** 单元测试：signature 去掉 #；带 `challenges.cloudflare.com` iframe 的假 PageState 能被 detect 命中。`python -m hands run cases/extra_captcha.yaml` 用命令行原流程仍可走完（人工步骤用测试注入函数）；`python run_tests.py --rounds 3` 全过。

### K14：数据目录 DATA_ROOT [云]
**文件：** `src/hands/config.py`
**依赖：** 无
**步骤：** `DATA_ROOT = Path(os.environ["HANDS_DATA_DIR"])` 若设置，否则 `PROJECT_ROOT`；`_abs` 和 `load_config` 的默认 config 路径改为相对 DATA_ROOT；trajlib 默认路径同样相对 DATA_ROOT。
**验证：** 不设环境变量时 `pytest -q`、`run_tests.py --only T01,T03` 通过；设置 `HANDS_DATA_DIR=/tmp/d` 并放一份 config.yaml 后，运行产物出现在 `/tmp/d/runs`。

---

## 阶段 C：面板后台

### K15：PanelService 与浏览器控制 [云]
**文件：** `src/panel/service.py`、`src/panel/server.py`
**依赖：** K7、K13、K14
**步骤：**
1. `PanelService(config)`：持有 BrowserManager、SampleStore、夹具服务句柄、崩溃次数、状态字符串。
2. `browser_start/stop/restart/set_mode(headless)`；`front()` = `Page.bringToFront` + `Browser.getWindowForTarget` + `Browser.setWindowBounds`（放在屏幕右半边）。
3. 崩溃看护任务：每 0.5 秒检查；无任务且 `browser.crashed` 已置位 → 状态 `crash_restarting` → `restart()` → 计数 +1 → `running`。
4. `shutdown()`：停看护 → 停任务 → 关浏览器 → 停夹具服务。
5. 路由：`POST /api/browser/start|stop|restart|mode|front`、`GET /api/state`（此时只含 browser、page 字段）。
**验证：** 云端脚本：start → state.running；用 `kill -9` 杀 Chrome 主进程 → 5 秒内 state 先变 `crash_restarting` 再变 `running`、crashes=1；stop → 无残留进程（`profile_chrome_pids` 为空）。

### K16：状态推送与 WS 集线器 [云]
**文件：** `src/panel/server.py`、`src/panel/service.py`
**依赖：** K15
**步骤：**
1. `/ws?token=`：连接后立即发一次完整 state；订阅 `STATUS`，有事件立即推 `{"t":"event"}`，并每 0.5 秒推一次完整 state（含 wait 已等时长、monitor 快照、phase）。
2. 多个客户端连接互不影响；断开时清理订阅。
**验证：** 云端脚本连接 WS，导航到 F4 慢请求页：0.5 秒内收到 `wait.what == "network_quiet"`，之后 elapsed_ms 递增，monitor.pending 含 `/api/delay`；断开重连后立即收到完整 state。

### K17：推流 Screencaster [云]
**文件：** `src/panel/preview.py`、`src/panel/server.py`
**依赖：** K16
**步骤：**
1. `start()`：在 `browser.page.session` 上 `Page.startScreencast`（参数可配）；每帧：立即 ack、存最新帧、通知集线器（集线器给每个客户端发送最新帧，客户端上一帧未发完就跳过）。
2. 订阅 `STATUS` 的 page_changed → 在新 session 上重新开始。
3. 统计：2 秒滑动窗口 fps；客户端通过 WS 回报 `{"t":"shown","seq","at"}` → 计算延迟中位数。
4. 无界面且有客户端时默认开；有界面默认关；`POST /api/preview {on}` 手动切换。
**验证：** 云端：开 `panel_bench` 页 5 秒，服务端统计 fps ≥ 50；脚本模拟客户端回报，延迟中位数 < 100 ms；点击 F11 夹具的新标签页链接后 2 秒内收到新页面的帧（帧头 url 变化）。

### K18：导航与元素接口 [云]
**文件：** `src/panel/service.py`、`src/panel/server.py`
**依赖：** K15
**步骤：** `POST /api/nav {url}`、`/api/nav/back`、`/api/nav/reload`（有任务时 409）；`GET /api/elements`：DomReader 读当前页，返回 `[{id, role, name, box, visible}]`。
**验证：** 打开 F1 静态页：elements 数量与 `fixtures/f01_static/expected.json` 一致；后退、刷新后 state.page.url 正确。

---

## 阶段 D：前端主界面

### K19：主界面骨架 [云]
**文件：** `static/index.html`、`style.css`、`app.js`
**依赖：** K16
**步骤：** 布局（预览区 / 顶部栏 / 右侧标签页）；API 封装自动带令牌；WS 连接、断线 1 秒后重连、重连先拉 `/api/state`；顶部栏：状态灯（五种颜色 + 文字）、启动 / 停止 / 重启、无界面 / 有界面切换、崩溃次数、地址栏（任务运行中只读）、后退、刷新、"正在运行：xxx"。
**验证：** 云端用第二个 Chrome（profile `profiles/panel_ui_test`）打开面板，脚本点"启动"→ 状态灯文字变"运行中"；刷新面板页面后 1 秒内状态恢复。

### K20：预览画布与叠加层 [云]
**文件：** `app.js`、`style.css`
**依赖：** K17、K19
**步骤：**
1. 收到二进制帧 → `createImageBitmap` → 画到帧画布；绘制完成后回报 `shown`。
2. 叠加画布：元素框（编号）、最近目标框、计划轨迹（由绿到红）、落点、过冲点、光标；每项一个开关；坐标换算 CSS ↔ 画布（帧宽 / 设备宽）。
3. 显示 fps 和延迟。
**验证：** 云端：F1 静态页打开"元素框"，脚本读出叠加层里每个框的画布坐标换算回 CSS，与 `/api/elements` 的 box 相差 ≤ 1 px；截图保存供目视（AC6）。

### K21：等待 / 网络面板与任务树 / 时间线面板 [云]
**文件：** `app.js`、`index.html`
**依赖：** K19
**步骤：** "网络"标签：当前等待（中文名、已等秒数、上限）、加载阶段、未完成请求、失败请求（状态码）、console 报错；"任务"标签：任务树（缩进、状态色、步数 / 上限、子任务摘要、活动节点高亮）+ 时间线表（步号、工具、参数、是否生效、变化、三段耗时）。网址都以纯文本显示，点击只复制。
**验证：** 打开 F4 慢请求页：网络标签显示"等待：网络安静"且秒数增长；打开 F9 故障页：显示 404 和 `FIXTURE_ERROR_42`（脚本读 DOM 断言）。

---

## 阶段 E：四种使用方式与人工介入

### K22：JobManager 与互斥 [云]
**文件：** `src/panel/jobs.py`、`server.py`、`app.js`
**依赖：** K16
**步骤：** `JobManager`：`start(kind, params)`（锁被占用返回 409 和当前任务标题）、`stop()`（置标志）、`continue_human()`；JobInfo 进 state；前端在有任务时禁用其他启动按钮、模式切换、停止浏览器、地址栏。
**验证：** 启动一个长任务时，再 POST `/api/jobs` 得 409；UI 里其他三个启动按钮 `disabled`（脚本读 DOM）。

### K23：演示任务 DemoJob [云]
**文件：** `jobs.py`、`app.js`
**依赖：** K20、K22、K5
**步骤：** 打开 F12 夹具 → 按 中心、八方向、小按钮 顺序点击（总带 Humanizer，参数取当前设置，种子取设置或新生成）→ 每次点击后读 `window.__clicks` 最后一条判断命中 → 推 `action_done`（轨迹、计划 / 实际耗时、命中）→ 结束推 `job_end`；RunRecorder 标签 `panel-demo`。前端：结果表 + 本轮轨迹画在叠加层，上一轮改为灰色虚线保留。
**验证：** 打开轨迹跑一轮：9 次全部命中；用同一种子再跑一轮，两轮 `points` 完全相等；`runs/` 下出现 `panel-demo` 目录。

### K24：用例任务 CaseJob 与停止 [云]
**文件：** `jobs.py`、`app.js`、`cases/panel_stop.yaml`
**依赖：** K22、K13
**步骤：** `GET /api/cases` 列出 `cases/*.yaml`；CaseJob：需要时启动夹具服务 → `PanelCaseRunner`（传 should_stop、before_action、humanizer_factory、handoff_fn、on_step）→ on_step 推任务树和时间线 → 结束写 JobInfo.result（通过、原因、报告相对路径）；`panel_stop.yaml`：在 F8 懒加载页反复滚动，步数上限 30。
**验证：** 面板运行 `t13_reddit_yes`：通过，任务树出现子任务和摘要；运行 `panel_stop` 第 3 步时 POST stop：在第 3 或第 4 步结束后停下，结果原因 `user_stopped`。

### K25：指定目标点击 TargetJob [云]
**文件：** `jobs.py`、`app.js`
**依赖：** K18、K22
**步骤：** 前端"指定目标"模式：鼠标在预览上移动时用缓存的 `/api/elements` 高亮命中的最小元素框；点击 → POST `{kind:"target", x, y}`（CSS 坐标）→ 后台重新读元素、选包含该点的最小可见元素 → `executor.click` → 推结果。
**验证：** F1 静态页点第 2 个按钮：页面状态显示该按钮被点；F5 夹具点跨源 iframe 里的按钮：iframe 内结果文字与 expected.json 一致。

### K26：手动模式 ManualJob 与输入转发 [云]
**文件：** `src/panel/inputs.py`、`jobs.py`、`app.js`
**依赖：** K20、K22
**步骤：** ManualJob 占锁；前端手动模式下把鼠标移动（节流到每帧一次）、按下、抬起、滚轮、按键发到 WS，换算成 CSS 坐标；隐藏 textarea 接收输入法，`compositionend` 发 `{"t":"text"}`；inputs.py 转成 `Input.dispatchMouseEvent` / `dispatchKeyEvent` / `insertText`；非手动模式收到的输入丢弃。
**验证：** 云端脚本通过 WS 发送：点 F7 表单输入框 → text "你好abc" → 点提交 → 页面显示"你输入了：你好abc"；F8 懒加载页发滚轮 → 第二页出现。（中文输入法本身在 AC14 由用户验证）

### K27：面板内人工介入 [云]
**文件：** `jobs.py`、`app.js`、`fixtures/f13_turnstile/*`、`cases/extra_turnstile.yaml`
**依赖：** K24、K26
**步骤：**
1. `panel_handoff`：截图 → 状态 `need_human`、推 `need_human` 事件 → 打开推流、允许手动输入 → `await continue_event` → 返回当前 page。
2. 前端：醒目提示条"需要人工：xxx"、自动切到预览和手动模式、"继续"按钮。
3. 夹具：`pass.html` 嵌入 Turnstile 测试密钥 `1x00000000000000000000AA`，`interactive.html` 用 `3x00000000000000000000FF`；用例 `extra_turnstile.yaml` 打开 pass.html → 等 → 读页面 → complete。
**验证：** 面板运行 `extra_captcha`（假页）：出现 need_human；脚本模拟人工（打开 `gate.html?solve=1`）后 POST continue → 用例通过，报告有人工介入时间；运行 `extra_turnstile`（需联网）：need_human 只出现一次，continue 后通过，之后不再触发。

---

## 阶段 F：设置、历史、用户目录、有界面

### K28：设置表与"下一个动作生效" [云]
**文件：** `src/panel/settings.py`、`server.py`、`app.js`、`tests/unit/test_settings.py`
**依赖：** K22
**步骤：** `SPECS` 列出全部可调项（plan 3.4）；`LiveSettings.update(patch)` 全部校验通过才进 pending，否则返回逐项错误；`apply_now(config, humanizer)` 把 pending 合入 current 并写进 Config 和 Humanizer.cfg；所有任务的 `before_action` 调用它；`GET/PUT /api/settings`；前端设置表单（开关、数值、下拉、种子"固定 / 随机"、决策方式下拉里"视觉大模型（未接入）"禁用）、用途说明文字。
**验证：** 单元测试：越界值被拒且 current 不变；apply 后 Humanizer.cfg 生效。集成：演示任务第 3 次点击过程中 PUT `overshoot_prob=0`：第 3 次记录仍是旧值，第 4 次起为 0。

### K29：保存为默认与恢复默认 [云]
**文件：** `settings.py`、`tests/unit/test_settings.py`
**依赖：** K28
**步骤：** `save_default(path)`：复制为 `.bak`；逐行扫描 config.yaml，按缩进维护键路径栈，命中的标量值原地替换（保留行尾注释）；缺失的键追加到对应段末尾；`restore()`：current = 代码默认值。
**验证：** 单元测试：用仓库里的 config.yaml 副本改 3 个值后保存，再 `load_config` 读回值正确，注释行数不变，`.bak` 与原文件一致。

### K30：运行历史 [云]
**文件：** `src/panel/history.py`、`server.py`、`app.js`
**依赖：** K15
**步骤：** 扫描 `runs/*/report.json`（按目录名倒序，最多 200 条）→ 时间、标签、通过数 / 总数；`/runs/{dir}/{path}` 只读静态映射（防目录穿越）；前端历史列表，点击在沙箱 iframe（`sandbox="allow-same-origin"`，无 allow-top-navigation）里打开 report.html，报告内链接点击被前端拦截并提示。
**验证：** 列表条数与 `runs/` 下含 report.json 的目录数一致；`/runs/../config.yaml` 返回 404；报告能在 iframe 里显示。

### K31：用户目录面板 [云]
**文件：** `src/panel/profile.py`、`server.py`、`app.js`、`fixtures/f14_cookie/index.html`
**依赖：** K15
**步骤：** `info()` 路径 + 占用字节；`clear(confirm)`：浏览器在运行或 confirm != "CLEAR" 时拒绝，否则删除重建；前端两步确认（先点"清空"，再输入"清空"二字）；夹具页：`?set=1` 写 Cookie `panel_cookie=yes`（一年有效），页面显示当前 Cookie。
**验证：** set=1 → 停止浏览器 → 再启动 → 打开夹具页显示 `panel_cookie=yes`；浏览器运行时 clear 返回 409；停止后 clear → 再启动 → 夹具页不再显示该 Cookie。

### K32：有界面模式行为 [云 + 本机]
**文件：** `service.py`、`preview.py`、`app.js`
**依赖：** K17、K15
**步骤：** 切到有界面：推流停止，预览区显示"浏览器窗口在屏幕上"和"调到前台""打开推流"两个按钮；切回无界面：自动恢复推流。
**验证：** [云] 切换后 state.preview.on 为 false，手动打开后收到帧。[本机] 点"调到前台"Chrome 窗口到前台（AC3 目视）。

---

## 阶段 G：真实网站采集与轨迹库回放

### K33：真实网站采集——注入与回传 [云]
**文件：** `src/panel/samples.py`、`jobs.py`
**依赖：** K22、K6
**步骤：**
1. `LISTENER_JS`：在隔离环境里注册 capture + passive 的 `pointermove/pointerdown/click` 监听，按 50 ms 批量调用绑定函数 `__panelSample`；只在收到"开题"消息后记录；记录 `pointerleave`（离开视口）。
2. RealSampleJob：要求有界面（无界面时 400"请先切到有界面"）；`Runtime.addBinding(name, executionContextName=世界名)`、`Page.addScriptToEvaluateOnNewDocument(source, worldName)`、对当前文档 `Page.createIsolatedWorld` + `Runtime.evaluate`；`Overlay.enable`。
3. 结束 / 停止：`removeScriptToEvaluateOnNewDocument`、`Runtime.removeBinding`、`Overlay.hideHighlight`；之后收到的绑定调用一律丢弃。
**验证：** [云] 用无界面 + 测试开关临时放行，在 F1 静态页用 `Input.dispatchMouseEvent` 走一次"起点 → 目标"：样本入库，source=real、domain=127.0.0.1、不含页面文字；结束后再发鼠标事件，库条数不变；页面主环境里 `Object.keys(window)` 不含任何新增全局变量。

### K34：真实网站采集——出题与界面 [云 + 本机]
**文件：** `jobs.py`、`collect.js` / `app.js`
**依赖：** K33
**步骤：** 出题：起点 = 上一题落点（第一题由用户随意点一下作为起点）；从 DomReader 可点可见元素里挑满足方向（优先格子最缺的方向）和距离 150–600 的元素，`Overlay.highlightNode` 橙色高亮；没有合适元素时提示"该方向本页无合适目标，已跳过"；点中导致跳转时提示"页面已跳转，样本已保存"并给"后退"按钮。面板里显示当前题目和进度格。
**验证：** [云] F11 跳转夹具上模拟点中会跳转的链接：样本已保存，后退回原页面。[本机] AC30 由用户在 Bing 首页实际采 5 条。

### K35：轨迹库回放在面板中切换 [云]
**文件：** `settings.py`、`jobs.py`、`app.js`
**依赖：** K5、K28、K23
**步骤：** 设置里"轨迹来源"切到真实轨迹库后，演示 / 用例 / 指定目标三种任务的 Humanizer 都带上 SampleStore 的 Library；演示结果表多一列"样本编号 / 退回模板"。
**验证：** 用 K9 生成的样本（每方向至少 1 条）跑演示：9 次命中、每次有 sample_id、首末点误差 ≤ 1 px；删掉"右"方向全部样本再跑：该次 fallback=true；`config.yaml` 设 `source: library` 后 `python run_tests.py --only T21` 通过。

---

## 阶段 H：Windows 窗口与打包

### K36：窗口入口与单实例 [本机为主]
**文件：** `src/panel/app.py`
**依赖：** K19
**步骤：** 打包环境设置 `HANDS_DATA_DIR=<exe 目录>/data`（首次运行从包内复制 config.yaml、cases、fixtures 到 data）；`panel.lock`（pid、端口、令牌）：存在且 pid 存活且 `/api/focus` 成功 → 退出；否则接管。后台线程跑 asyncio + aiohttp；主线程 `webview.create_window("Hands 控制面板", url)`、`webview.start(private_mode=True)`；`/api/focus` 让窗口 `restore()` + `show()`；窗口关闭 → `service.shutdown()` → 删锁。Chrome 找不到 → state 带 `need_chrome_path`，前端显示路径输入框，保存写入 config.yaml。
**验证：** [云] 单元级：锁文件逻辑（存活 / 失效 pid 两种）测试。[本机] 双击两次只有一个窗口且被切到前台；关闭窗口后无 Chrome 残留。

### K37：外部跳转拦截 [本机为主]
**文件：** `app.py`、`app.js`、`server.py`
**依赖：** K36、K30
**步骤：** 先验证 pywebview 能否拿到 WebView2 控件（`window.native` → `CoreWebView2`）：能 → 订阅 `NavigationStarting`、`FrameNavigationStarting`、`NewWindowRequested`，非面板地址取消并通过 WS 推"已拦截"事件；不能 → 兜底：前端全局拦截 `a` 点击、`window.open`、`dragover/drop`，报告 iframe 沙箱化，CSP 已在 K7 加上；两种情况都在验收报告注明。
**验证：** [本机] 在历史报告里点 Bing 链接 → 提示"已拦截"，窗口仍在面板；把一个网址拖进窗口 → 不跳转（AC21）。

### K38：PyInstaller 打包 [本机]
**文件：** `packaging/panel.spec`
**依赖：** K36、K37
**步骤：** onedir；入口 `src/panel/app.py`；数据：`src/panel/static`、`fixtures`、`cases`、`config.yaml`（作为首次运行模板）；隐藏导入 aiohttp、webview 平台模块；产物 `dist/HandsPanel/HandsPanel.exe`。
**验证：** [本机] `pyinstaller packaging/panel.spec` 成功；在 Windows 沙盒里双击，10 秒内面板可用（AC19）。

---

## 阶段 I：面板验收与全量回归

### K39：面板验收框架 [云]
**文件：** `tests/panel/common.py`、`run_panel_tests.py`
**依赖：** K22
**步骤：** `PanelCtx`：起后台（随机端口、令牌）、REST / WS 客户端、等待 state 条件、可选打开 UI 浏览器（独立 profile）读 DOM；`run_panel_tests.py [--only P05] [--rounds N]` 输出 编号 / 通过 / 耗时 / 原因 表并写 `panel_acceptance.json`。
**验证：** 放一个空的 `p00_smoke.py`（启动、取 state）跑通并出表。

### K40：面板自动验收项 [云]
**文件：** `tests/panel/p01_*.py` …
**依赖：** K39 及对应功能任务
**步骤：** 每个自动 / 自动+目视的 AC 一个文件：P01=AC1、P02=AC2、P03=AC3（云端部分）、P04=AC4、P05=AC5、P06=AC6、P07=AC7、P08=AC8、P09=AC9、P10=AC10、P11=AC11、P12=AC12、P13=AC13、P15=AC15、P16=AC16、P18=AC18、P20=AC20、P21=AC21（CSP 与前端拦截部分）、P22=AC22（依赖清单检查）、P23=AC23、P24=AC24、P25=AC25、P27=AC27、P28=AC28；目视项把截图存进运行目录。
**验证：** `python run_panel_tests.py` 云端全部通过（P15 的 Turnstile 部分需联网）。

### K41：全量回归与写回 [云]
**依赖：** K40
**步骤：** `pytest -q`；`python run_tests.py`（T17 50 轮）；`python run_panel_tests.py`；把全部改动写回用户项目文件夹，附改动文件清单；更新 `docs/panel/acceptance.md`（云端结果）和 handover。
**验证：** 三项全过；改动清单与实际写回文件一致。

### U4：本机验收 [本机]
**依赖：** K41
**步骤：** 依次：U2 数值填入后跑 P05（AC5）；AC3 目视；AC14 中文输入法；AC17 其余部分（作废三种、回看、导入导出）；AC19 打包程序（沙盒、断网）；AC21 拦截；AC26 三档缩放；AC29 两种真实验证码；AC30 真实网站采集；在本机再跑一遍 `run_panel_tests.py` 和 `run_tests.py`。
**验证：** 每项结果记入 `docs/panel/acceptance.md`。

---

## 执行顺序

```
U1（用户，随时）
K1 ─→ U2（用户）
K2 ─┬→ K3 → K4 → K5
    └→ K6 → K7 → K8 → K9 → K10 ─→ U3（用户开始采集）
K11 → K12 → K13 ; K14
        └──────────→ K15 → K16 → K17 → K20
                       ├→ K18      └→ K19 → K21
                       └→ K22 → K23, K24 → K27, K25, K26
                              → K28 → K29 ; K30 ; K31 ; K32
                              → K33 → K34 ; K35
                              → K36 → K37 → K38（本机）
                              → K39 → K40 → K41 → U4（用户）
```
阶段末回归：A、B、C、E、F、G 各一次（`--rounds 3`），I 跑满 50 轮。
