# 使用者自制任务书 + 平板远程 Tasks

日期：2026-11-16　状态：待审批（阶段三）　依据：`spec.md`、`plan.md`（均已批准）

约定：
- 单元测试命令：`python -m pytest -q tests/unit/<文件>`；"全部单测"指 `python -m pytest -q`。
- 面板检查：`python run_panel_tests.py --only <编号>`。新检查编号接在现有之后：任务书用 `B01…`，远程用 `R01…`。
- 涉及桌面驱动的验收会真实移动鼠标：运行前提醒用户别碰鼠标键盘。
- 每完成一组相关任务，**等用户要求时**才提交 git。

## 文件清单

| 操作 | 文件 | 职责 |
|---|---|---|
| 修改 | `pyproject.toml` | panel 依赖加 `segno` |
| 修改 | `config.yaml`、`src/hands/config.py` | 新增 `remote:` 段（port、idle_close_s、cloudflared） |
| 修改 | `.gitignore` | `tasks/.bak/`、`panel_devices.json` |
| 修改 | `src/hands/expand/tree.py` | `rev`、`rule` 来源、`apply_rule`、`new_terminal` |
| 修改 | `src/hands/expand/rank.py` | `parse_price` 从长文字里找价位 |
| 修改 | `src/hands/vlm/digest.py` | `text_lines()` |
| 新建 | `src/hands/checks.py` | `HandoffChecker`、`respect_interval` |
| 修改 | `src/hands/runner.py` | CaseRunner 改用 `HandoffChecker` 与共用访问间隔 |
| 新建 | `src/hands/taskbook/__init__.py` | 包 |
| 新建 | `src/hands/taskbook/blocks.py` | `ParamSpec`、`BlockSpec`、`REGISTRY`、`BUILTIN_LISTS` |
| 新建 | `src/hands/taskbook/book.py` | `TaskBook`、解析 / 序列化 / 参数替换 / 保存备份 / rev |
| 新建 | `src/hands/taskbook/validate.py` | `Problem`、`check()` |
| 新建 | `src/hands/taskbook/convert.py` | `detect()`、`from_legacy()` |
| 新建 | `src/hands/taskbook/guide.py` | `build()` |
| 新建 | `src/hands/taskbook/engine.py` | `Item`、`TreeOp`、`NodeRun`、`NodeCtx`、`run_node()` |
| 新建 | `src/hands/taskbook/web.py` | search / scroll_all / open / page |
| 新建 | `src/hands/taskbook/pick.py` | pick_list / read_fields |
| 新建 | `src/hands/taskbook/calc.py` | ratio / rank / filter / compare |
| 新建 | `src/hands/taskbook/runner.py` | `TaskbookRunner` |
| 新建 | `src/hands/taskbook/report.py` | `build_result`、`report_html` |
| 新建 | `src/hands/vlm/prompts/pick_list.md`、`pick_fields.md`、`taskbook_guide.md` | 提示词与指导提示词模板 |
| 修改 | `src/hands/__main__.py` | `book` 子命令，`expand` 作别名 |
| 删除 | `src/hands/expand/judge.py`、`subtask.py`、`run.py`，`prompts/expand_judge.md`、`expand_node.md`，`tests/unit/test_expand_judge.py` | 退役 |
| 修改 | `tasks/fixture_transformers.yaml`、`fixture_dinner.yaml`、`transformers.yaml`、`dinner.yaml`、`dinner_yelp.yaml` | 改写为 `taskbook/1` |
| 新建 | `tests/unit/data/legacy/*.yaml` | 5 份旧格式原件 |
| 新建 | `src/panel/books.py` | `BookService` |
| 新建 | `src/panel/devices.py` | `Device`、`PairCode`、`DeviceStore` |
| 新建 | `src/panel/tunnel.py` | `Tunnel` |
| 新建 | `src/panel/remote.py` | `RemoteAccess`、远程中间件、白名单 |
| 修改 | `src/panel/server.py` | 路由抽成可复用；任务书 / 远程管理 / 配对路由；WS 缩略图模式；来源 |
| 修改 | `src/panel/jobs.py` | 任务种类 `book`；改树带 rev；操作记来源 |
| 修改 | `src/panel/preview.py` | 远程缩略图（640 宽、≤1 帧 / 秒） |
| 修改 | `src/panel/app.py` | 退出时关闭远程访问 |
| 新建 | `src/panel/static/build.html/js/css`、`m.html/js/css`、`pair.html` | 搭建工具、平板外壳、配对页 |
| 修改 | `src/panel/static/tree.html/js/css`、`index.html`、`app.js`、`style.css` | 响应式与 rev；任务书入口；远程访问卡片 |
| 新建 | `tests/unit/test_taskbook_*.py`、`test_checks.py`、`test_remote_*.py`、`test_tunnel.py` | 单元测试 |
| 修改 | `tests/unit/test_expand_tree.py`、`test_expand_report.py` | 适配 |
| 新建 | `tests/panel/books_check.py`、`remote_check.py`、`fake_cloudflared.py` | 面板检查 |
| 修改 | `tests/panel/checks.py` | 登记 B、R 两组检查 |
| 修改 | `tests/acceptance/d12_expand.py`、`d17_dinner.py` | 改跑夹具任务书；d12 加重跑选项 |
| 新建 | `tests/acceptance/d18_blocks.py` | 积木行为三驱动验收 |

---

## 第一组：准备与底层改造

### T1: 依赖与配置
**文件：** `pyproject.toml`、`config.yaml`、`src/hands/config.py`、`.gitignore`
**依赖：** 无
**步骤：**
1. `pyproject.toml` 的 `panel` 可选依赖加 `segno`；执行 `pip install segno`。
2. `config.yaml` 末尾加 `remote:` 段：`port: 8791`、`idle_close_s: 7200`、`cloudflared: ""`（留空 = 从 PATH 找），每项写中文注释。
3. `config.py` 读入 `remote` 段，缺省值同上（字典或小 dataclass，与现有段的写法一致）。
4. `.gitignore` 加 `tasks/.bak/` 与 `panel_devices.json`（数据目录内的文件，按实际数据目录位置写规则）。

**验证：** `python -c "from hands.config import load_config; c=load_config(); print(c.remote)"` 打印出三项默认值；`python -c "import segno"` 不报错；全部单测通过。

### T2: 树的版本号 rev
**文件：** `src/hands/expand/tree.py`、`tests/unit/test_expand_tree.py`
**依赖：** 无
**步骤：**
1. `ExpandTree.__init__` 加 `self.rev = 0`；`_changed()` 里 `self.rev += 1`（所有改动都经过它）。
2. `snapshot()` 输出 `rev`。
3. 测试：初始化后 rev ≥ 1；`delete`、`add_group`、`grant`、`consume`、`apply` 各使 rev 增加；只读查询不变。

**验证：** `python -m pytest -q tests/unit/test_expand_tree.py` 全过。

### T3: 规则改树 apply_rule（加为值 / 加到某键 / 标末端 / 删除）
**文件：** `src/hands/expand/tree.py`、`tests/unit/test_expand_tree.py`
**依赖：** T2
**步骤：**
1. 新增 `apply_rule(nid, op: str, words: list[str], key: str | None = None, new_terminal=False, why: dict | None = None) -> list[dict]`，`actor` 固定为 `rule`，返回被拒项 `[{word, rejected}]`。
2. 映射到现有逻辑：
   - `add_to_current`：当前节点还不是键 → 每个词走 `_new_key(n, word)`（当前词成为键）；已经是键 → 走 `_map_add(n.word, word)` 并给已展开的同名键节点补子节点。
   - `add_to_key`：`key` 必须在 `mapping` 里，否则整条拒绝"不是已知的键"；然后同 `add_value` 分支。
   - `terminal`、`delete`：同 `apply` 里对应分支。
3. `why` 写进节点 `reasons`（带 step、依据清单原文）。`_event` 的 actor 记 `rule`。结尾 `_changed("rule")`。
4. 测试：未知键被拒；同值重复被拒；当前词成为新键后插入 roots 的位置与 `apply(new_key)` 一致；连续两条 `add_to_current` 第二条看到当前词已是键（不重复插 root）。

**验证：** `python -m pytest -q tests/unit/test_expand_tree.py` 全过。

### T4: new_terminal（新值直接定为末端叶子）
**文件：** `src/hands/expand/tree.py`、`tests/unit/test_expand_tree.py`
**依赖：** T3
**步骤：**
1. 加 `self.terminal_values: set[tuple[str, str]]`；`apply_rule(..., new_terminal=True)` 时把 `(键, 词)` 记进去。
2. `_new()` 生成子节点时，若 `(父词, 词)` 在集合里，`state="terminal"`，原因记"任务书设为末端"。
3. `next_node` 不会挑到这类节点（它们不是 pending），无需改动；在测试里确认。
4. 测试：点餐式场景——当前词「便宜」下加两个店名并 `new_terminal`，展开后两节点都是 terminal、`leaves()` 含它们、不消耗次数。

**验证：** `python -m pytest -q tests/unit/test_expand_tree.py` 全过。

### T5: parse_price 从长文字里找价位
**文件：** `src/hands/expand/rank.py`、`tests/unit/test_expand_rank.py`（没有就新建）
**依赖：** 无
**步骤：**
1. 现有逻辑保留；整串解析失败时，用正则在文字中找：独立的 `$`～`$$$$`（前后不是字母数字）、`¥/￥/$` 加数字、`人均 N`、`N-M 元`、`$N–$M`，取第一个命中再走原换算。
2. 测试：`"Italian · $$ · Open now"` → 2 档；`"人均 ¥85 · 川菜"` 换算后档位正确；`"4.5 (1,203 reviews)"` → None；原有用例不变。

**验证：** `python -m pytest -q tests/unit/test_expand_rank.py` 全过。

### T6: digest.text_lines()
**文件：** `src/hands/vlm/digest.py`、`tests/unit/test_digest_text_lines.py`
**依赖：** 无
**步骤：**
1. 新增 `text_lines(text: str, limit: int = 120, width: int = 80) -> list[str]`：按换行切分 → 去首尾空白 → 去空行 → 按规范化文字去重（保留首次）→ 每行截到 `width` 字 → 取前 `limit` 行。
2. 测试：重复行只留一次；长行截断；超过 120 行只取前 120；全角空格行被去掉。

**验证：** `python -m pytest -q tests/unit/test_digest_text_lines.py` 全过。

### T7: HandoffChecker（转人工检查抽出）
**文件：** `src/hands/checks.py`、`tests/unit/test_checks.py`
**依赖：** 无
**步骤：**
1. 读 `runner.py` 中 `_attempt` 里"captcha.detect → iframe 已处理跳过 → confirm.login_hit → pending_handoff → gate.pause('handoff') → handoff_fn / captcha.handoff → gate.resume → 记 handoffs → _handled.add(signature)"整段。
2. 原样搬成 `class HandoffChecker`：构造参数 `browser, config, confirm, gate, handoff_fn, input_fn, log`；状态 `handled: set`、`pending: str | None`、`records: list[dict]`；方法 `async check_page(state, save_dir) -> tuple[page | None, dict | None]`（返回处理后的页面和这次的记录，没转人工则 `(None, None)`）；`set_pending(hit)`。超过 3 次仍未解决抛 `HandoffFailed`。
3. 同文件加 `async respect_interval(last: dict, url: str, min_interval: dict)`，从 `CaseRunner._respect_interval` 原样搬出（按域名最小访问间隔）。
4. 测试（假 state / 假 handoff_fn / 假 gate）：验证码命中 → 暂停原因 `handoff`、调用 handoff_fn、恢复；同一签名第二次不再转；登录命中走同一路径；`pending` 优先于检测；超过 3 次抛异常。

**验证：** `python -m pytest -q tests/unit/test_checks.py` 全过。

### T8: CaseRunner 改用 HandoffChecker
**文件：** `src/hands/runner.py`
**依赖：** T7
**步骤：**
1. `CaseRunner.__init__` 建 `self.checker = HandoffChecker(...)`；`_attempt` 里那一段换成调用 `checker.check_page`；`_pending_handoff` 改为 `checker.set_pending`；`_handoffs` 记录从 `checker.records` 取。
2. `_respect_interval` 改为调 `checks.respect_interval`。
3. 失败次数上限、`tree.fail("captcha not resolved: …")` 文本保持原样。

**验证：** 全部单测通过；`python run_tests.py --only T09,T11,T12`（含验证码 / 登录 / 导航用例）结果与改动前一致。

---

## 第二组：任务书格式、校验、转换

### T9: 积木注册表 blocks.py
**文件：** `src/hands/taskbook/__init__.py`、`blocks.py`、`tests/unit/test_taskbook_blocks.py`
**依赖：** 无
**步骤：**
1. 定义 `ParamSpec`、`BlockSpec`（字段同 plan）。
2. `REGISTRY: dict[str, BlockSpec]`，按 plan 的 12 行表逐个写参数：类型、必填、默认（如 `screens=6`、`ignore_case=True`、`next_text=["下一页","Next","›"]`）、范围（`at_least` 0–1、`max` 1–50、`k` ≥1）、choices（`mode`、`op`、`action`）、中文 help。`flow` 类型用于 `open.flow`。
3. `BUILTIN_LISTS = ("当前词", "所在键", "已知词", "已知键")`；`TEMPLATE_VARS = ("word", "key", "q")`。
4. `registry_json()` 输出供前端和指导提示词使用的纯字典。
5. 测试：12 个 id 都在；每个 `produces=True` 的积木都有 `as` 参数；`cost` 只有 search/open/page 为 1；`registry_json()` 可 JSON 序列化。

**验证：** `python -m pytest -q tests/unit/test_taskbook_blocks.py` 全过。

### T10: 任务书解析与序列化
**文件：** `src/hands/taskbook/book.py`、`tests/unit/test_taskbook_book.py`
**依赖：** T9
**步骤：**
1. 定义 `TaskBook`（字段同 plan）、`BookError`。
2. `parse(text, name=None) -> TaskBook`：YAML 读入；`format` 必须是 `taskbook/1`，否则 `BookError("不认识的格式版本")`；`groups` 支持 `- key: [v…]` 列表写法，转成 `list[tuple]`；`flow` 每项必须是单键字典；`limits` 缺省 `budget=120, keep_tabs=30, trial_budget=5`；`rev = sha256(text)` 前 16 位；status 先记 `ok`。
3. `dump(book) -> str`：按固定字段顺序输出、`allow_unicode=True`，`parse(dump(b))` 与 b 等价。
4. 测试：两份 plan 里的示例能解析；字段缺省值正确；往返一致；错误版本报错。

**验证：** `python -m pytest -q tests/unit/test_taskbook_book.py` 全过。

### T11: 开放参数与模板替换
**文件：** `src/hands/taskbook/book.py`、`tests/unit/test_taskbook_book.py`
**依赖：** T10
**步骤：**
1. `bind(book, values: dict) -> TaskBook`：按 `params` 的 type 把字符串值转成 int / float / bool / text；缺省取 default；未声明的参数名报错；结果写进 `values`。
2. `render(template: str, word, key, values) -> str`：替换 `{word}`、`{key}`、`{参数名}`；`{q}` 留给 search 积木；未知变量报 `BookError`。
3. 测试：`--param city=北京` 生效；类型转换；未知变量报错。

**验证：** `python -m pytest -q tests/unit/test_taskbook_book.py` 全过。

### T12: 保存（备份、rev 冲突、另存为）
**文件：** `src/hands/taskbook/book.py`、`tests/unit/test_taskbook_book.py`
**依赖：** T10
**步骤：**
1. `load(path) -> TaskBook`（文件名去扩展名作 `name`）。
2. `save(dir, name, text, rev: str | None, save_as=False) -> str（新 rev）`：
   - 名称只允许字母数字下划线连字符；
   - 另存为时文件已存在 → `BookConflict("已存在同名任务书")`；
   - 覆盖时磁盘 rev ≠ 请求 rev → `BookConflict("已被另一端修改，请刷新")`；
   - 覆盖前把旧文件复制到 `dir/.bak/<name>.<YYYYmmdd-HHMMSS>.yaml`；写入用临时文件再替换。
3. 测试（临时目录）：正常覆盖产生备份；rev 不一致抛冲突且文件不变；另存为同名报错。

**验证：** `python -m pytest -q tests/unit/test_taskbook_book.py` 全过。

### T13: 校验——参数级
**文件：** `src/hands/taskbook/validate.py`、`tests/unit/test_taskbook_validate.py`
**依赖：** T9、T10
**步骤：**
1. `Problem` 数据类；`check(book) -> list[Problem]`。
2. 逐步检查 `flow[i]`：积木 id 在注册表；未知参数名；必填齐全；类型（int/float/bool/list/map/choice）；范围；choices；`rank` 的 `k` 与 `top_k` 二选一；`page` 的 `to` 与 `next` 二选一。
3. `open.flow` 递归，step 写成 `flow[3].open.flow[0]`。
4. 每条 `why` 用中文说明，如"必填参数 region 没填"。
5. 测试：每种错误各一例，断言 step / block / param / why。

**验证：** `python -m pytest -q tests/unit/test_taskbook_validate.py` 全过。

### T14: 校验——引用与全局
**文件：** `src/hands/taskbook/validate.py`、`tests/unit/test_taskbook_validate.py`
**依赖：** T13
**步骤：**
1. 清单引用（`in`、`from`、`use`、`when`、`words` 等 listref 参数）：只能是内置清单或**前面**积木的 `as`（子流程可引用外层已产出的，外层可引用子流程产出的）；同名 `as` 重复给出提示。
2. 模板变量：`url`、`query`、`extra` 里的 `{x}` 必须是 `word` / `key` / `q` 或已声明参数；`search.url` 必须含 `{q}`。
3. 全局：`start` 在某组的键或值里；`groups` 非空；`limits` 为正整数；`output.type` 为 `leaves` / `ranked`；ranked 时 `sort` 字段形如 `名称:asc|desc`。
4. `flow` 为空 → 问题"节点流程为空"；`status` 由调用方根据问题是否为空设成 `ok` / `draft`。
5. 测试：引用后面才产出的清单报错；引用内置清单通过；未声明变量报错；两份示例零问题。

**验证：** `python -m pytest -q tests/unit/test_taskbook_validate.py` 全过。

### T15: 旧格式转换
**文件：** `src/hands/taskbook/convert.py`、`tests/unit/data/legacy/`、`tests/unit/test_taskbook_convert.py`
**依赖：** T10、T14
**步骤：**
1. 把 `tasks/` 下 5 份旧文件**复制**到 `tests/unit/data/legacy/`（原文件在 T29–T31 改写）。
2. `detect(text) -> "taskbook" | "legacy" | "unknown"`：有 `format: taskbook/1` 为新；能被 `expand.spec.load_task_type` 读为旧；否则 unknown。
3. `from_legacy(text) -> (str 新文本, list[Problem])`：照搬名称、初始组、起始词、上限、输出方式；搜索方式写成一个 `search` 积木；自然语言条件拼进 `note`，前缀"【需要用积木改写】"；`flow` 只含该 `search`，并附一条 Problem"由旧格式转换，需要用积木补全节点流程"，status=`needs_rewrite`。
4. 测试：5 份原件都能转换；转换结果能 `parse`；status 为 needs_rewrite；初始组与原件一致。

**验证：** `python -m pytest -q tests/unit/test_taskbook_convert.py` 全过。

---

## 第三组：计算积木

### T16: Item 与 ratio
**文件：** `src/hands/taskbook/engine.py`（只放数据类）、`calc.py`、`tests/unit/test_taskbook_calc.py`
**依赖：** T9
**步骤：**
1. `engine.py` 先定义 `Item`、`TreeOp`、`NodeRun` 数据类（字段同 plan），`Item.to_dict()`。
2. `calc.norm(s, ignore_case=True)`：调用 `vlm.extract.normalize`，再按需转小写。
3. `ratio(items, top, words: list[str], at_least, current, drop_current) -> (out, log)`：每个词数前 `top` 条里 `text` 或 `context` 包含它的条数；比例 ≥ 阈值输出 `Item(text=词, origin={ratio, hits:[序号]})`；`log` 记每个词的比例。
4. 测试：10 条里 8 条含 megatron → 0.8 输出；7 条不输出；当前词被去掉；大小写与全角不影响。

**验证：** `python -m pytest -q tests/unit/test_taskbook_calc.py` 全过。

### T17: rank 与 filter
**文件：** `src/hands/taskbook/calc.py`、`tests/unit/test_taskbook_calc.py`
**依赖：** T16
**步骤：**
1. `rank(items, k=None, top_k=None)`：第 k 名（1 起，越界返回空并在 log 写"只有 N 条"）或前 k 个。
2. `filter(items, alias, stop, current, known, drop_current, drop_known, only_known, ignore_case, contains) -> (out, dropped)`：顺序为别名归一 → 停用词 → 去当前词 → 去已知 / 只留已知 → 去重；`dropped` 每项 `{text, reason}`，reason 用 spec F13 的词：别名归一 / 停用词 / 当前词 / 已有词 / 不在已知词。别名归一后 `text` 改为目标词，`origin.alias_from` 记原文。
3. 测试：「擎天柱」「Optimus Prime」别名归一为 optimus 后作为当前词被滤掉；`contains` 模式下「Megatron (Transformers)」归一为 megatron；only_known 留下已知词；去重保留首次。

**验证：** `python -m pytest -q tests/unit/test_taskbook_calc.py` 全过。

### T18: compare
**文件：** `src/hands/taskbook/calc.py`、`tests/unit/test_taskbook_calc.py`
**依赖：** T5、T16
**步骤：**
1. `compare(items, field, op, value) -> (ok, unknown, dropped)`：取 `item.fields[field].text`（无此字段 → unknown，reason"未知"）。
2. 字段名含"价"或值形如 `$`…时用 `parse_price`；否则取第一个数字；`contains` 做文字包含。比较值用同样方式解析，解析失败抛 `ValueError`（引擎记为积木错误）。
3. 不满足的进 `dropped`，reason"比较不满足"。
4. 测试：`$$ <= $$` 满足；`$$$` 不满足；无价位进 unknown；评分 `>= 4.5`；`contains` 中文。

**验证：** `python -m pytest -q tests/unit/test_taskbook_calc.py` 全过。

---

## 第四组：引擎与网页 / 4b 积木

### T19: 引擎主循环
**文件：** `src/hands/taskbook/engine.py`、`tests/unit/test_taskbook_engine.py`
**依赖：** T9、T16
**步骤：**
1. `NodeCtx` 数据类：`page_fn`（取当前页面）、`executor_fn`、`gate`、`client`、`checker`、`node_dir`、`should_stop`、`remaining()`、`settings`、`log`、`book`、`tree`、`node`。
2. `run_node(ctx) -> NodeRun`：初始化清单表 4 张内置清单（当前词 = 节点词；所在键 = 父词；已知词 = 树 mapping 所有键与值；已知键 = mapping 的键），`已知词` / `已知键` 每次读取时现算。
3. 按顺序取积木：`await gate.wait()` → `should_stop()` 为真则 `stop="cancel"` 结束 → 积木 cost > remaining → `stop="budget"` 结束。
4. 分派表 `HANDLERS[id]`，每个处理函数签名 `async (ctx, params, lists, rec) -> list[Item] | None`；有 `as` 时写入 lists；`rec` 是本块记录字典（params、inputs 摘要、out、picked、rejected、dropped、error、cost、t）。
5. 处理函数抛异常 → `rec.error=str(e)`，产出空清单，继续。
6. `NodeRun.blocks` 依次追加 rec；`cost` 累加。
7. 测试（假处理函数注入）：顺序执行；第二块抛错后第三块仍执行并拿到空清单；cost 不够时停在该块之前；should_stop 生效。

**验证：** `python -m pytest -q tests/unit/test_taskbook_engine.py` 全过。

### T20: 引擎——tree / stop / 计算积木接线、网页后检查
**文件：** `src/hands/taskbook/engine.py`、`tests/unit/test_taskbook_engine.py`
**依赖：** T17、T18、T19
**步骤：**
1. `tree` 处理函数：从 `use` 清单取词，生成 `TreeOp` 追加到 `NodeRun.ops`（不改树），rec 记依据原文。
2. `stop`：`when` 清单非空 → `NodeRun.stop = action`，跳出循环。
3. ratio / rank / filter / compare 接到 calc，`dropped` 写进 rec；compare 的 `unknown_as` 写进 lists。
4. search / open / page 处理函数返回后，引擎调用 `ctx.checker.check_page(state, node_dir)`；转人工记录写进 rec。
5. 测试：两条 tree 积木产出两条 TreeOp、顺序保留；stop 后面的积木不执行；compare 的未知清单可被后续 tree 引用。

**验证：** `python -m pytest -q tests/unit/test_taskbook_engine.py` 全过。

### T21: 网页积木 search
**文件：** `src/hands/taskbook/web.py`、`tests/unit/test_taskbook_web.py`
**依赖：** T7、T11、T19
**步骤：**
1. `search(ctx, p, lists, rec)`：`render(query)` → `quote_plus` → 填进 `url` 的 `{q}` → `extra` 各值 render 后作为查询参数追加 → `respect_interval` → `page.navigate(url)` → `wait_settled`；rec 记最终网址；截图存 `node_dir/sNN.png`。
2. 测试（假 page 记录调用）：网址编码正确（中文、空格）；extra 追加；访问间隔被调用。

**验证：** `python -m pytest -q tests/unit/test_taskbook_web.py` 全过。

### T22: 网页积木 scroll_all
**文件：** `src/hands/taskbook/web.py`、`tests/unit/test_taskbook_web.py`
**依赖：** T21
**步骤：**
1. `bottom_then_top`：循环 `executor.scroll(down, 一屏)`，每次读页面滚动位置，不再变化或满 30 屏停；然后滚回顶部。rec 记屏数。
2. `to_text`：`build_page_state` 后找 `name/text` 包含 `text` 的元素，滚到它进入视口；找不到 → 退回 bottom_then_top，rec 记"没找到「…」，改为滚到底再回顶"。
3. 测试（假 executor + 假滚动位置序列）：位置不变时停；30 屏上限；to_text 找不到时退回。

**验证：** `python -m pytest -q tests/unit/test_taskbook_web.py` 全过。

### T23: 网页积木 open 与 page
**文件：** `src/hands/taskbook/web.py`、`tests/unit/test_taskbook_web.py`
**依赖：** T21
**步骤：**
1. `open`：取 `lists[from][n-1]`（越界 → rec 记"没有第 n 条"，返回空）；按 `item.element` 在当前 PageState 里找元素（找不到再按文字找）；驱动 Capabilities 支持标签页 → `executor.open_tab`，否则 `executor.click`；`wait_settled` → 递归 `run_flow(ctx, p.flow, lists)`（子流程产出合并回 lists）→ `back` 为真时关闭标签页 / `page.back()`。
2. `page`：`to` 给定时找名字等于该页码的链接；否则找名字匹配 `next_text` 任一项的链接；点击后等待稳定；找不到 → rec"没有下一页"，不消耗次数（cost 记 0）。
3. 测试：支持标签页与不支持两条路径；子流程产出可在外层引用；越界不报错；没有下一页不扣次数。

**验证：** `python -m pytest -q tests/unit/test_taskbook_web.py` 全过。

### T24: 4b 提示词
**文件：** `src/hands/vlm/prompts/pick_list.md`、`pick_fields.md`
**依赖：** 无
**步骤：**
1. `pick_list.md`：说明图上编号框；任务"哪些编号是「{region}」里的条目，按页面从上到下"；只回 `{"numbers":[…]}`；这一屏没有就回 `{"numbers":[]}`；不要解释。给一个正例一个空例。
2. `pick_fields.md`：列出元素清单与文本行（`T1…`）；对每个字段回 `{"fields":[{"name":"价位","no":"T12"}, …]}`，找不到填 `null`。
3. 与现有提示词的变量写法一致（读一份现有提示词照着写）。

**验证：** 文件存在；`python -c` 用现有提示词加载函数读入两份，变量全部替换后没有残留 `{`。

### T25: pick_list——单屏选号
**文件：** `src/hands/taskbook/pick.py`、`tests/unit/test_taskbook_pick.py`
**依赖：** T19、T24
**步骤：**
1. `_pick_screen(ctx, region, anchor_y) -> (items, picked, rejected)`：`build_page_state` → `PageDigest.build` → 用 `pick_list.md` 问 4b（现有客户端，带重问机制，最多 3 次）→ 解析 numbers。
2. 每个编号：`digest.to_id(no)` 为空 → rejected "编号不存在"；元素 y < anchor_y → rejected "在锚点之前"；否则 `Item(text=name 或 text 原文, element=id, url, context=元素 context)`。
3. 测试（替身客户端返回固定 JSON）：编号换原文；不存在编号被拒；锚点之上被拒；三次格式错误后返回空并记原因。

**验证：** `python -m pytest -q tests/unit/test_taskbook_pick.py` 全过。

### T26: pick_list——多屏、锚点、去重、停止条件
**文件：** `src/hands/taskbook/pick.py`、`tests/unit/test_taskbook_pick.py`
**依赖：** T22、T25
**步骤：**
1. 有 `anchor`：先在元素里找文字等于任一锚点的元素，滚到它；记录它的页面 y 作为 anchor_y；找不到 → rec"没找到锚点"，产出空。
2. 循环每屏 `_pick_screen`，按 `calc.norm(text)` 去重；截图存 `node_dir`；停止条件：够 `max`；已有条目且本屏为空；滚动位置不再变化；屏数达 `screens`。
3. rec 写 picked（每屏的编号）、rejected、屏数。
4. 测试：第 2 屏为空时停；去重跨屏生效；max 截断；锚点找不到。

**验证：** `python -m pytest -q tests/unit/test_taskbook_pick.py` 全过。

### T27: read_fields
**文件：** `src/hands/taskbook/pick.py`、`tests/unit/test_taskbook_pick.py`
**依赖：** T6、T25
**步骤：**
1. 当前屏 PageDigest 元素清单 + `text_lines(state 正文)` 编成 `T1…Tn`，用 `pick_fields.md` 问 4b。
2. 每个字段：`no` 为元素编号 → 元素原文；`T12` → 该行原文；`null` / 不存在 → `fields` 不含该字段，rec 记"未找到"。
3. 产出一个 `Item`：`text` = `name_field` 的值（没找到用页面标题），`fields` = 各字段 Item，`url` = 当前网址。
4. 测试：元素编号与文本行编号都能取值；未找到字段记录；值逐字来自页面。

**验证：** `python -m pytest -q tests/unit/test_taskbook_pick.py` 全过。

---

## 第五组：运行器、样例、命令行、退役

### T28: report.py（结果与报告页）
**文件：** `src/hands/taskbook/report.py`、`tests/unit/test_expand_report.py`
**依赖：** T4
**步骤：**
1. 把 `expand/run.py` 的 `report_html`、`build_result` 搬为函数 `report_html(tree, book, run_dir)`、`build_result(tree, book)`。
2. 叶子每条附依据：节点 `reasons` 里的 step、依据原文、网址。
3. ranked 输出：用 `output.sort` 字段从叶子 `fields` 取值排序（价位用 `parse_price`，其它取数字），`unknown: last` 排在后面单列，`exclude` 不参与；`group_by: key` 按所在键分组取前 n。
4. 报告页加"每次消耗"一节：读 `book/nodes/*/blocks.json` 列出各积木产出与滤掉的项。
5. 改 `test_expand_report.py` 引用新位置；加 ranked 的 unknown last / exclude 两例。

**验证：** `python -m pytest -q tests/unit/test_expand_report.py` 全过。

### T29: 改写 fixture_transformers.yaml
**文件：** `tasks/fixture_transformers.yaml`
**依赖：** T14、T15
**步骤：**
1. 读旧文件和 `tests/acceptance/dcommon.py` 的仿搜索夹具页结构（结果区块、相关搜索区块的文字与锚点）。
2. 按 plan 变形金刚示例写 `taskbook/1`：search 指向夹具服务地址（用开放参数 `base` 默认夹具地址）、scroll_all、两个 pick_list、ratio、filter（别名表）、两个 tree。
3. 用 `validate.check` 检查零问题。

**验证：** `python -c "from hands.taskbook import book, validate; b=book.load('tasks/fixture_transformers.yaml'); print(validate.check(b))"` 输出 `[]`。

### T30: 改写 fixture_dinner.yaml
**文件：** `tasks/fixture_dinner.yaml`
**依赖：** T14、T15
**步骤：**
1. 读旧文件和点餐夹具页结构（榜单、详情页的店名 / 价位 / 评分位置）。
2. 按 plan 点餐示例写：search → pick_list 榜单 → rank → open（read_fields）→ compare → 两个 tree（new_terminal）；`output: ranked`，`unknown: last`。
3. 校验零问题。

**验证：** 同 T29 的命令换成本文件，输出 `[]`。

### T31: 改写三份真实站点任务书
**文件：** `tasks/transformers.yaml`、`dinner.yaml`、`dinner_yelp.yaml`
**依赖：** T29、T30
**步骤：**
1. 以两份夹具版为模板，换成真实 Google / Bing / Yelp 搜索网址；transformers 加 `extra: {hl: "{lang}"}`、锚点 `[相关搜索, Related searches, People also search for]`、别名表含「擎天柱」「Optimus Prime」→ optimus。
2. 每份 `note` 写明"人工验收样例"。
3. 校验零问题。

**验证：** 三份各跑一次 T29 的检查命令，均输出 `[]`。

### T32: guide.py 与指导提示词模板
**文件：** `src/hands/taskbook/guide.py`、`src/hands/vlm/prompts/taskbook_guide.md`、`tests/unit/test_taskbook_blocks.py`
**依赖：** T9、T29、T30
**步骤：**
1. 模板写：用途、文件格式（逐字段说明）、内置清单、模板变量、常见错误（引用后面的清单、忘了 `as`、search 网址没有 `{q}`、把条件写成自然语言）、`{{BLOCKS}}`、`{{EXAMPLES}}` 两个占位。
2. `build()`：从 `REGISTRY` 生成每个积木的"id（中文名）· 谁执行 · 用途 · 参数表（名、类型、必填、默认、范围、说明）· 产出 · 消耗"；示例读两份夹具任务书原文。
3. 测试（AC8）：指导提示词里出现全部 12 个 id 及其每个参数名；`registry_json()` 的参数集合与提示词一致；两份示例原文都在其中。

**验证：** `python -m pytest -q tests/unit/test_taskbook_blocks.py` 全过。

### T33: TaskbookRunner 主循环
**文件：** `src/hands/taskbook/runner.py`、`tests/unit/test_taskbook_runner.py`
**依赖：** T3、T4、T20、T23、T26、T27、T28
**步骤：**
1. 构造参数同 plan；建 `ExpandTree(groups, start, budget)`（试跑用 trial_budget），`HandoffChecker`。
2. 运行目录下建 `book/`：写 `taskbook.yaml`（原文副本）、`params.json`（values、budget、trial、driver）。
3. 循环：`next_node` → 无节点则结束 → `mark_searched` → 记下树 rev 与节点 version → `run_node` → `tree.consume(cost)` → 节点或其祖先 version 在执行期间变了 → 本轮作废、`unsearch`、记 rec → 否则逐条 `apply_rule`（why 带 step 与依据原文）→ 写 `nodes/NNNN_<词>/blocks.json` → 快照 `snapshots/NNNN.json` → `on_change(snapshot)`。
4. 标签页保留：TreeOp `keep_tab` 为真且驱动支持时把当前标签页挂到节点，超过 `keep_tabs` 关最早的（沿用 ExpandRunner 的做法）。
5. 测试（假引擎返回预设 NodeRun）：顺序、消耗、快照数量、作废逻辑、目录文件齐全。

**验证：** `python -m pytest -q tests/unit/test_taskbook_runner.py` 全过。

### T34: 等待决策、停、试跑、结束
**文件：** `src/hands/taskbook/runner.py`、`tests/unit/test_taskbook_runner.py`
**依赖：** T33
**步骤：**
1. 次数用完或 `NodeRun.stop == "handoff"` → 进入等待决策（沿用 ExpandRunner 的 `decision` 事件 / 等待接口）：再给 N 次（`tree.grant`）继续，结束并输出则 `tree.finish`。
2. `stop == "finish"` → 直接结束；`"cancel"` → 按取消结束。
3. 结束：`build_result` → `result.json`、`report.html`；试跑时运行标签带 `trial`。
4. 用户改树接口（删、设末端、设待查、加组、再给、结束）带 `rev` 与 `source`：rev 不一致抛 `PermissionError("已被另一端修改，请刷新")`；成功后追加 `edits.jsonl`。
5. 测试：用完后等待、grant 后继续；handoff 停；rev 冲突抛错；edits.jsonl 内容含来源。

**验证：** `python -m pytest -q tests/unit/test_taskbook_runner.py` 全过。

### T35: 夹具任务书全流程（替身模型、假页面）
**文件：** `tests/unit/test_taskbook_runner.py`
**依赖：** T29、T30、T34
**步骤：**
1. 用假页面（按网址返回预设 PageState 与元素）+ 替身 4b（按 region 返回对应编号），跑 fixture_transformers 前 5 次，断言搜索顺序与 d12 的 `EXPECT` 前 5 项一致。
2. 跑 fixture_dinner，断言 ranked 前 2 与 d17 标准答案一致。

**验证：** `python -m pytest -q tests/unit/test_taskbook_runner.py` 全过。

### T36: 命令行 book 子命令
**文件：** `src/hands/__main__.py`
**依赖：** T15、T34
**步骤：**
1. 新增 `book` 子命令：`<任务书.yaml> [--param k=v …] [--budget N] [--trial] [--driver …] [--label …]`；读文件 → `convert.detect`：旧格式打印"这是上一章的旧格式，已转换，需要用积木改写节点流程"并退出码 2；校验有问题逐条打印并退出码 2。
2. 用 TaskbookRunner 运行；`expand_menu` 改为调新 runner 的等待决策接口；打印树沿用 `print_tree`。
3. `expand` 子命令指向同一函数（别名）。

**验证：** `python -m hands book --help` 显示参数；`python -m hands book tests/unit/data/legacy/fixture_transformers.yaml` 打印旧格式提示、退出码 2；`python -m hands expand tasks/fixture_transformers.yaml --budget 0 --trial` 能进入运行流程（不需联网即停在等待决策，输入结束）。

### T37: 退役旧判定
**文件：** 删除 `expand/judge.py`、`subtask.py`、`run.py`、`prompts/expand_judge.md`、`expand_node.md`、`tests/unit/test_expand_judge.py`
**依赖：** T36、T38（验收脚本已切换）、T40（jobs 已切换）、T43（首页不再用旧入口）
**步骤：**
1. `Grep` 全仓库 `expand.run|expand.judge|expand.subtask|ExpandRunner|expand_judge|expand_node`，逐个改为新模块或删除。
2. 删除文件；`tree.py` 的 `JUDGE_OPS` 与 `apply` 保留（旧单测仍用），注释注明供兼容。

**验证：** 再次 Grep 无残留；全部单测通过；`python -m panel --help` 能启动参数解析。

---

## 第六组：验收脚本、面板任务书服务

### T38: d12 / d17 改跑任务书
**文件：** `tests/acceptance/d12_expand.py`、`d17_dinner.py`
**依赖：** T35
**步骤：**
1. 用 `book.load("tasks/fixture_*.yaml")` + `TaskbookRunner` 取代 ExpandRunner；替身模型改为按 region 回编号（替身读夹具页元素，按区块选号）。
2. 判定逻辑不变：d12 比对 `EXPECT` 15 步顺序；d17 比对三类前 2。

**验证：** `python tests/acceptance/d12_expand.py --driver cdp` 与 `python tests/acceptance/d17_dinner.py --driver cdp` 输出通过。（桌面驱动版本留到验收阶段，运行前提醒用户别碰鼠标。）

### T63: 积木行为验收脚本 d18_blocks（AC3，阶段四补）
**文件：** `tests/acceptance/d18_blocks.py`
**依赖：** T35
**步骤：**
1. 用仿搜索夹具 + 替身模型，按驱动参数 `--driver cdp|playwright|desktop` 跑一份专用的小任务书（search 带 extra → scroll_all → pick_list 相关搜索 → open 第 1 条（read_fields，含一个不存在的字段）→ page → pick_list），替身在一屏里故意给一个不存在的编号。
2. 逐项判定并打印：网址含附加参数；抽到视口外的相关搜索；每条原文是页面文本子串且不重复；不存在编号出现在 rejected；open 后回到原页；缺失字段记"未找到"；翻页后的 pick_list 来自第 2 页网址。
3. 退出码：全过 0，否则 1。

**验证：** `python tests/acceptance/d18_blocks.py --driver cdp` 全部判定通过。

### T64: d12 重跑选项（AC16）
**文件：** `tests/acceptance/d12_expand.py`
**依赖：** T38
**步骤：**
1. 加 `--replay <运行目录>`：读该目录 `book/taskbook.yaml` 与 `params.json`，用同一替身再跑一遍。
2. 比较两次最后一份快照的节点（词、父、状态）与 mapping，一致则通过。

**验证：** 先跑一次 `d12_expand.py --driver cdp`，再用 `--replay` 指向这次的运行目录，输出"重跑一致"。

### T39: BookService
**文件：** `src/panel/books.py`、`tests/unit/test_panel_books.py`
**依赖：** T12、T15、T32
**步骤：**
1. `BookService(tasks_dir)`：`list()`（每份：name、title、status、rev、params；解析失败的列出并标 draft）、`get(name)`、`validate(text)`、`import_(text)`（detect → 转换或解析 → 校验）、`save(name, text, rev, save_as)`、`preview(text)`、`guide()`。
2. `preview`：解析 → 新建 ExpandTree → 循环 `next_node` / `mark_searched` / 不做任何改树 / `_finalize`，直到结束或 200 步，返回初始快照与处理顺序。
3. 测试：列表含 5 份且状态正确；保存冲突抛 `BookConflict`；导入旧格式得到 needs_rewrite；预览顺序与夹具无改树时的顺序一致。

**验证：** `python -m pytest -q tests/unit/test_panel_books.py` 全过。

### T40: JobManager 任务种类 book
**文件：** `src/panel/jobs.py`
**依赖：** T34、T39
**步骤：**
1. `start("book", params)`：参数 `name | text`、`values`、`budget`、`trial`；校验不通过抛 `ValueError`（问题列表放进消息）；流程含 pick_list / read_fields 时检查 Ollama（复用 `/api/vlm/status` 的检查函数），不可用抛 `RuntimeError("本机 Ollama 不可用")`。
2. `_book(job, params)`：仿 `_expand` 接 TaskbookRunner，`on_change` 推快照；运行标签 `book-<name>` / `book-<name>-trial`。
3. `expand_edit` 增加 `rev`、`source` 参数并转给 runner；pause / resume / confirm / stop 增加 `source` 参数，写入当前运行的 `edits.jsonl`。
4. 删除 `kind="expand"` 分支与 `expand_types`（`/api/expand/start` 在 T41 改为转发）。

**验证：** 全部单测通过；`python run_panel_tests.py` 中原有关联展开相关检查（若有）改为 book 后通过。

### T41: 任务书路由
**文件：** `src/panel/server.py`
**依赖：** T39、T40
**步骤：**
1. 加路由：`GET /api/books`、`GET /api/books/blocks`、`GET /api/books/guide`、`GET /api/books/steps`、`POST /api/books/{validate,import,save,preview,run}`、`GET /api/books/{name}`（注意 `blocks` / `guide` / `steps` 要先于 `{name}` 注册）。
2. `save` 冲突返回 409 与 `"已被另一端修改，请刷新"`；`import` 支持 JSON `{text}` 与 multipart 文件。
3. `steps?run=`：用 `history.safe_path` 定位运行目录，读 `book/nodes/*/blocks.json`，返回列表（截图转为 `/runs/...` 网址）。
4. `/api/expand/edit` 读 `rev`、来源（本机入口固定"电脑"）；冲突 409；`/api/expand/start` 转发为 book。
5. 页面路由 `/build` → `build.html`。

**验证：** 面板跑起来后 `curl` 带令牌请求 `/api/books` 返回 5 份；`/api/books/guide` 返回含 12 个积木 id 的文本。

### T42: 面板检查 B 组
**文件：** `tests/panel/books_check.py`、`tests/panel/checks.py`
**依赖：** T41
**步骤：**
1. B01 列表与读取；B02 校验返回逐条问题；B03 导入旧格式 needs_rewrite；B04 保存两次（第二次用旧 rev）→ 409，`tasks/.bak/` 有备份（用临时任务目录）；B05 预览；B06 试跑 fixture_transformers（替身模型、夹具站点）→ steps 返回 5 次消耗、每次含截图与积木产出；B07 改树带旧 rev → 409。
2. 在 `checks.py` 的 `CHECKS` 里登记。

**验证：** `python run_panel_tests.py --only B01,B02,B03,B04,B05,B06,B07` 全过。

### T43: 首页任务书入口
**文件：** `src/panel/static/index.html`、`app.js`、`style.css`
**依赖：** T41
**步骤：**
1. 原"关联展开"启动区改为"任务书"：下拉选任务书（显示状态，草稿 / 需要改写不可选运行）、按开放参数生成输入框、上限输入、"运行""试跑""编辑"（跳 `/build?name=`）"新建"按钮。
2. 启动失败显示返回的问题清单。

**验证：** 面板预览里选 fixture_transformers → 参数框出现 → 点"编辑"打开搭建工具；`read_console_messages` 无错误。

---

## 第七组：搭建工具与任务树页

### T44: build 页面骨架与样式
**文件：** `src/panel/static/build.html`、`build.css`
**依赖：** T41
**步骤：**
1. 结构：顶栏（名称、标题、状态、按钮区：校验 / 预览 / 试跑 / 保存 / 另存为 / 导入 / 指导提示词）；左栏（开放参数、初始组、起始词、上限、输出）；右栏（流程列表 + 选中积木的参数表单）；底部（问题列表）；试跑面板（抽屉）。
2. CSS：≥ 900px 左右两栏，窄屏上下；按钮最小 44px 高；无横向滚动；颜色变量与现有 `style.css` 一致。
3. 注入 `{{TOKEN}}` 的方式与现有页面一致（远程入口时为空，见 T52）。

**验证：** 面板预览打开 `/build`，宽屏两栏；`resize_window` 到 768 宽为上下排列且无横向滚动。

### T45: build.js——读取、参数与初始组编辑
**文件：** `src/panel/static/build.js`
**依赖：** T44
**步骤：**
1. 启动：取 `/api/books/blocks`；URL 有 `name` 时取 `/api/books/{name}`，否则空白模板；持有 `state = {book 对象, text, rev, problems}`。
2. 开放参数表（增删、类型下拉、默认值）；初始组（增删组、增删值、上下移动）；起始词下拉（从组里取）；上限与输出表单。
3. 每次修改把对象转回 YAML 文本（前端用简单序列化：只输出本格式用到的结构，字符串一律加引号）。

**验证：** 打开 fixture_transformers，增加一个组再删掉，文本与原文语义一致（`/api/books/validate` 零问题）。

### T46: build.js——流程列表与积木表单
**文件：** `src/panel/static/build.js`
**依赖：** T45
**步骤：**
1. 流程列表：每行"序号 · 中文名 · 主要参数摘要 · 产出名"；增（从 12 个积木中选）、删、上移、下移；`open` 显示可展开的子流程，子流程同样可编辑。
2. 选中一行显示表单：按 `ParamSpec.type` 生成控件（text 输入、int/float 数字框带范围、bool 开关、choice 下拉、list 多行文本、map 两列表、listref / words_ref 下拉——只列内置清单与**前面**积木的 `as`）。
3. 参数旁显示 help。

**验证：** 在空白任务书里依次加 search、pick_list、tree，tree 的 `use` 下拉只出现内置清单与 pick_list 的产出名；把 tree 上移到 pick_list 前面后，下拉不再出现该产出名且校验报错。

### T47: build.js——校验、保存、导入、指导提示词
**文件：** `src/panel/static/build.js`
**依赖：** T46
**步骤：**
1. 校验：调 `/api/books/validate`，问题列表每条可点击定位到对应步骤与参数（高亮）。
2. 保存 / 另存为：带 rev；409 弹出"已被另一端修改，请刷新"，提供"重新载入"按钮。
3. 导入：选择文件或粘贴文本 → `/api/books/import` → 载入编辑器，立即显示问题；旧格式顶部显示"需要用积木改写"。
4. 指导提示词：弹层显示全文，"复制"与"下载 .md"。

**验证：** 导入 `tests/unit/data/legacy/dinner.yaml` 显示需要改写；在两个标签页打开同一任务书，先后保存，第二个弹出冲突提示；复制按钮把文本写入剪贴板（`javascript_tool` 读 `navigator.clipboard` 不可用时检查下载链接）。

### T48: build.js——预览与试跑面板
**文件：** `src/panel/static/build.js`、`build.css`
**依赖：** T47
**步骤：**
1. 预览：调 `/api/books/preview`，用树页同款绘制函数画初始树，并列出处理顺序。
2. 试跑：上限输入（默认 trial_budget）→ `/api/books/run`（trial，可用未保存文本）→ 订阅 `/ws` 状态；运行中显示进度与暂停 / 停止。
3. 结束或每次快照后拉 `/api/books/steps?run=`：每次消耗一张卡片（节点词、截图缩略、各积木一行：产出数量，展开看原文与依据元素、4b 选号与被拒、滤掉的项及原因、改树操作）。
4. 试跑结束后"回到编辑"保持编辑器内容。

**验证：** 在面板预览里对 fixture_transformers 试跑 5 次（夹具站点 + 替身模型配置），出现 5 张卡片，展开第一张能看到"相关"清单原文与被滤掉的别名项。

### T49: 任务树页响应式与 rev
**文件：** `src/panel/static/tree.html`、`tree.js`、`tree.css`
**依赖：** T41
**步骤：**
1. 修改请求带快照 `rev`；409 时提示"已被另一端修改，请刷新"并自动重新拉树。
2. 窄屏：节点操作按钮改为点节点后弹出的操作条；字号与按钮 ≥ 44px；无横向滚动（树过宽时节点名折行）。
3. 来源：节点原因里显示 `rule` 来源的步骤与依据原文。

**验证：** 面板预览 `resize_window` 到 768×1024 打开 `/tree`，无横向滚动；用两个标签页改同一棵树，后者收到冲突提示。

---

## 第八组：远程访问后端

### T50: DeviceStore（配对码与设备凭证）
**文件：** `src/panel/devices.py`、`tests/unit/test_remote_devices.py`
**依赖：** T1
**步骤：**
1. `Device`、`PairCode` 数据类；`DeviceStore(path, clock=time.time)`。
2. `issue(origin) -> PairCode`（`token_urlsafe(32)`，5 分钟；同一 origin 只保留最新一个）；`redeem(code, origin, ua) -> (Device, secret)`：码不存在 / 已用 / 过期 / origin 不同 → `PermissionError`；成功后 `used=True`，生成 `secret = token_urlsafe(32)`，存 `sha256`；名称"平板 · <浏览器名>"（从 UA 认 Safari / Chrome / Edge / Firefox）。
3. `verify(cookie) -> Device | None`（`<id>.<secret>`，`compare_digest`，更新 last_seen，last_seen 写盘节流 60 秒）；`rename`、`revoke`、`purge(kind="public")`、`list()`（不含摘要）。
4. 持久化 `<数据目录>/panel_devices.json`，只存 lan 设备（public 设备只在内存）。
5. 测试（假时钟）：过期、重用、错 origin、正确兑换；文件里没有明文凭证；吊销后 verify 失败；purge 只删 public。

**验证：** `python -m pytest -q tests/unit/test_remote_devices.py` 全过。

### T51: Tunnel（cloudflared 进程）
**文件：** `src/panel/tunnel.py`、`tests/panel/fake_cloudflared.py`、`tests/unit/test_tunnel.py`
**依赖：** T1
**步骤：**
1. `Tunnel(exe, port, on_down)`：`find_exe()`（配置路径 → `shutil.which("cloudflared")`）；`async start(timeout=30) -> url`：`asyncio.create_subprocess_exec(exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}")`，逐行读 stderr/stdout 匹配 `https://[a-z0-9-]+\.trycloudflare\.com`；超时 → 结束进程并抛 `RuntimeError("隧道 30 秒内没有拿到地址")`。
2. 后台任务等待进程退出 → 非主动关闭时调 `on_down()`；`async stop()` 结束进程（先 terminate，3 秒后 kill）。
3. `fake_cloudflared.py`：打印一行含 `https://fake-test.trycloudflare.com` 后常驻；带 `--exit-after N` 参数模拟意外退出、`--silent` 模拟拿不到地址。测试用 `sys.executable fake_cloudflared.py` 作为 exe（Tunnel 支持 exe 为列表）。
4. 测试：拿到地址；超时；意外退出触发 on_down；stop 不触发 on_down。

**验证：** `python -m pytest -q tests/unit/test_tunnel.py` 全过。

### T52: server 路由抽成可复用
**文件：** `src/panel/server.py`
**依赖：** T41
**步骤：**
1. 把 `create_app` 里注册路由的部分抽成 `register_routes(r, svc, *, token_for_page: str, source_of)`；本机入口 `create_app` 保持原中间件不变，调用它（`token_for_page=token`，`source_of=lambda req: "电脑"`）。
2. `page()` 用 `token_for_page` 替换 `{{TOKEN}}`；只在本机入口设置令牌 cookie。
3. 每个改动类处理函数（jobs pause/resume/confirm/stop、expand edit、books save/run）从 `source_of(request)` 取来源传给 jobs。
4. `Client` 增加 `mode`（`full` / `thumb`）与 `device_id` 字段，默认 full。

**验证：** 全部单测通过；`python run_panel_tests.py` 全部原有检查与改动前一致。

### T53: 远程中间件与配对路由
**文件：** `src/panel/remote.py`、`src/panel/static/pair.html`、`tests/unit/test_remote_access.py`
**依赖：** T50、T52
**步骤：**
1. `create_remote_app(svc, access)`：中间件依次——Host ∈ `access.allowed_hosts()`（局域网 `IP:端口`、隧道域名）否则 403；`/api/` 与 `/ws` 的 Origin 存在时必须 ∈ `access.allowed_origins()`；`/pair`、`/api/pair`、`/static/*` 放行；其余要求 `dev` cookie 且 `devices.verify` 通过，否则 401（页面请求跳 `/pair` 提示"请在电脑上扫码配对"）；路径不在 `ALLOW`（白名单常量：`/m`、`/tree`、`/build`、`/ws`、`/api/state`、`/api/jobs/{pause,resume,confirm,stop,continue}`、`/api/expand/{tree,edit}`、`/api/books*`、`/api/runs`、`/runs/*`）→ 403"只能在电脑上操作"。
2. 响应头：CSP 的 connect-src 为当前 origin 对应的 `ws(s)://host`；其余安全头同本机入口。
3. `register_routes(..., token_for_page="", source_of=lambda req: req["device"].name)`。
4. `/pair` 返回 `pair.html`：脚本读 `location.hash` 的 `c`，`history.replaceState` 清掉 hash，`POST /api/pair {code, ua}`；成功跳 `/m`，失败显示原因与"请在电脑上刷新二维码再扫"。
5. `POST /api/pair`：`redeem(code, 当前 origin, ua)` → `Set-Cookie dev=<id>.<secret>; HttpOnly; SameSite=Strict; Path=/`，https 下 `Secure`，lan 加 `Max-Age=31536000`；写 `remote.jsonl`。
6. 测试（aiohttp 测试客户端）：错 Host 403；跨站 Origin 403；无 cookie 401；配对后 `/api/state` 200；`/api/settings` 403；`/api/remote` 403；吊销后 401。

**验证：** `python -m pytest -q tests/unit/test_remote_access.py` 全过。

### T54: RemoteAccess（档位、站点、隧道、自动关闭、日志）
**文件：** `src/panel/remote.py`、`tests/unit/test_remote_access.py`
**依赖：** T51、T53
**步骤：**
1. `RemoteAccess(svc, cfg, devices, clock)`：`mode`、`lan_ip`、`public_url`、`codes`；`lan_ip()` 用 UDP 连接 `192.0.2.1` 取本机出口地址（不发包），失败退回 `socket.gethostbyname_ex` 中的私网地址。
2. `set_mode("off"|"lan"|"lan+public")`：
   - lan：起 `AppRunner(create_remote_app)` + `TCPSite("0.0.0.0", port)`；签 lan 配对码。
   - lan+public：先确保 lan，再 `Tunnel.start` → 记 url、加入允许的 Host / Origin → 签 public 配对码；失败时留在 lan 并返回原因（含 cloudflared 未安装的安装提示）。
   - off / 从 public 降到 lan：停隧道、`devices.purge("public")`、关闭这些设备的 WS。
3. `on_tunnel_down`：清公网状态与设备，推事件 `remote_changed {tunnel_down: true}`。
4. 空闲检查任务：每 60 秒；`public` 开着且 `max(公网设备 last_seen, 隧道开启时间)` 距今 > `idle_close_s` → 降到 lan，日志记"无人访问自动关闭"。
5. `revoke(id)`：`devices.revoke` + 关闭 `svc.clients` 中 `device_id` 相同的 WS。
6. 日志：`<数据目录>/logs/remote.jsonl`，一行一事件（时间、事件、设备名、档位），不写凭证与配对码。
7. `state()` 供 `/api/remote`：mode、地址、各码剩余秒数、设备列表、cloudflared 是否可用、防火墙提示是否显示（首次开 lan 时）。
8. 测试（假 Tunnel、假时钟、端口 0）：lan 开关；public 失败留在 lan；空闲超时降档并删公网设备；tunnel_down；revoke 断开连接。

**验证：** `python -m pytest -q tests/unit/test_remote_access.py` 全过。

### T55: 远程管理路由与二维码
**文件：** `src/panel/server.py`
**依赖：** T54
**步骤：**
1. 本机入口加：`GET /api/remote`、`POST /api/remote/mode`、`POST /api/remote/code`、`GET /api/remote/qr.svg?kind=lan|public`（`segno.make(f"{origin}/pair#c={code}")` 输出 SVG，`Content-Type: image/svg+xml`）、`POST /api/remote/devices/rename`、`POST /api/remote/devices/revoke`。
2. 配对码到期前后台自动换新（RemoteAccess 内每次 `state()` 发现过期即重签），二维码随之更新。
3. 这些路由不在远程白名单里（T53 的测试已覆盖 403）。

**验证：** 面板跑起来后带令牌 `POST /api/remote/mode {"mode":"lan"}` 返回地址；`GET /api/remote/qr.svg?kind=lan` 返回 SVG；手机或平板浏览器打开该地址的 `/m` 未配对时被拒。

### T56: WS 缩略图模式与来源
**文件：** `src/panel/server.py`、`src/panel/preview.py`
**依赖：** T52、T54
**步骤：**
1. `preview.py` 加 `thumb()`：从最新帧缩到宽 640、JPEG 质量 70，缓存到下一帧到来；记录生成时间。
2. 远程 `ws_handler`：`Client(mode="thumb", device_id=…)`；`frame_loop` 在 thumb 模式下每次发送间隔 ≥ 1 秒、发 `thumb()`；收到 `mouse/key/text` 一律丢弃。
3. 状态消息里加 `remote`（档位、设备数）和 `alert`（待确认 / 需要人工 / 次数用完三种，带说明），供平板提醒。
4. 远程 WS 握手时在 `access.ws_of[device_id]` 登记，断开时移除。

**验证：** T61 的面板检查 R04 覆盖；单独时在局域网配对后用 `aiohttp` 客户端连 `/ws`，10 秒内收到的二进制帧不超过 11 个且宽度为 640。

### T57: 退出时关闭远程访问
**文件：** `src/panel/app.py`（或面板入口）
**依赖：** T54
**步骤：**
1. 面板退出流程（窗口关闭、Ctrl+C、`--no-window` 结束）里调用 `await remote.set_mode("off")`，确保 cloudflared 子进程被结束。
2. 启动时若数据目录里有残留 public 设备（异常退出）一并清掉（public 不落盘，此步只确认）。

**验证：** 开 lan+public（用 fake_cloudflared 配置）后结束面板进程，`Get-Process` 中没有 fake_cloudflared 对应的 python 子进程；`remote.jsonl` 有关闭记录。

---

## 第九组：平板页面与电脑远程卡片

### T58: 平板外壳 m 页面——状态与控制
**文件：** `src/panel/static/m.html`、`m.js`、`m.css`
**依赖：** T56
**步骤：**
1. 顶部提示条（连接状态"已连接 / 已断开，正在重连"、提醒）；底部四个标签：状态、树、任务书、记录。
2. 状态页：当前任务、当前步骤、剩余次数、暂停原因；缩略画面（WS 二进制帧显示为图片）；待确认卡片（允许一次 / 拒绝）；暂停 / 恢复 / 取消按钮；需要人工时显示"需要到电脑前处理"与"取消任务"。
3. 所有请求不带令牌（靠 cookie），401 时跳 `/pair` 提示。
4. 触屏：按钮 ≥ 44px、竖横屏自适应、无横向滚动。

**验证：** 面板预览 `resize_window` mobile / tablet 打开远程地址的 `/m`（预览先用配对好的 cookie），状态页显示与电脑首页一致；`read_console_messages` 无错误。

### T59: 平板外壳——树、任务书、记录、提醒、重连
**文件：** `src/panel/static/m.js`、`m.css`
**依赖：** T48、T49、T58
**步骤：**
1. 树标签：`iframe` 嵌入 `/tree`；任务书标签：任务书列表（选、填参数、运行 / 试跑）与"编辑"（嵌入 `/build?name=`）、"新建"。
2. 记录标签：`/api/runs` 最近运行（试跑标记），点开看结果（叶子 / 排序输出）与报告页。
3. 提醒：状态里 `alert` 出现新值时顶部醒目条 + `navigator.vibrate([200,100,200])` + WebAudio 合成 0.3 秒提示音；首次点击页面时创建并 `resume()` AudioContext。
4. WS 断开每 2 秒重连；重连后拉 `/api/state` 与 `/api/expand/tree` 并通知 iframe 刷新。

**验证：** 面板预览中断开 / 恢复面板服务，提示条从"已断开"变回"已连接"且树一致；触发一次待确认，提示条出现（`javascript_tool` 检查 vibrate 被调用的打桩计数）。

### T60: 电脑首页远程访问卡片
**文件：** `src/panel/static/index.html`、`app.js`、`style.css`
**依赖：** T55
**步骤：**
1. 卡片：三档切换（关闭 / 仅局域网 / 局域网 + 公网）；当前地址；二维码（lan、public 各一，`<img src="/api/remote/qr.svg?kind=…&t=…">`）与剩余有效时间、"刷新二维码"；设备清单（名称、类型、配对时间、最近访问、改名、吊销）。
2. 提示：首次开局域网显示防火墙说明；没有 cloudflared 显示 `winget install Cloudflare.cloudflared`；`tunnel_down` 显示"公网隧道已断开"与"一键重开"。
3. 状态通过 `/ws` 的 `remote` 字段与定时 `GET /api/remote`（10 秒）刷新。

**验证：** 面板预览切到"仅局域网"出现地址与二维码；切到公网（fake_cloudflared 配置）出现 `fake-test.trycloudflare.com`；吊销一台设备后清单移除。

### T61: 面板检查 R 组
**文件：** `tests/panel/remote_check.py`、`tests/panel/checks.py`
**依赖：** T57、T59、T60
**步骤：**
1. R01 开 lan：用局域网地址、无 cookie 访问 `/m` 被拒；R02 签码 → 模拟平板 `POST /api/pair` → 带 cookie 访问 `/api/state` 200、`/api/settings` 403；R03 同一码再用失败；过期码（把时钟拨快）失败；R04 平板连 `/ws` 收到缩略图 ≤1 帧 / 秒、发送输入不生效；R05 吊销 → 平板 WS 被关闭、后续请求 401；R06 电脑与平板同时改树：后到的 409；R07 开公网（fake_cloudflared）→ 公网地址配对成功 → 降回 lan 后公网设备消失；R08 模拟隧道意外退出 → 状态 tunnel_down；R09 空闲超时（`idle_close_s` 设 2 秒）自动降档；R10 `remote.jsonl` 记录开关、配对、吊销、自动关闭且无凭证明文；R11 平板侧操作的暂停 / 改树在 `edits.jsonl` 里来源为设备名。
2. 在 `checks.py` 登记。

**验证：** `python run_panel_tests.py --only R01,R02,R03,R04,R05,R06,R07,R08,R09,R10,R11` 全过。

---

## 第十组：收尾

### T62: 全量回归
**文件：** 无（只运行）
**依赖：** T37、T38、T42、T61、T63、T64
**步骤：**
1. `python -m pytest -q`。
2. `python run_tests.py`（CDP）；`python run_panel_tests.py`。
3. `python tests/acceptance/d12_expand.py --driver cdp`、`d17_dinner.py --driver cdp`。
4. 记录结果到 `docs/taskbook/task.md` 末尾"开发进度"表（任务、状态、证据）。

**验证：** 以上命令全部通过；失败的先修再重跑，进度表写明证据。

---

## 执行顺序

```
T1 ─┬─ T50 ─┐
    └─ T51 ─┤
T2 → T3 → T4 ─────────────┐
T5 ──────────┐            │
T6 ──────────┼────────┐   │
T7 → T8      │        │   │
T7 ──────────┼──┐     │   │
T9 → T10 → T11 → T12  │   │
T9,T10 → T13 → T14 → T15  │
T9 → T16 → T17 ─┐     │   │
T5,T16 → T18 ───┤     │   │
T9,T16 → T19 → T20 (T17,T18)
T7,T11,T19 → T21 → T22 → (T25) T26
               T21 → T23
T24 → T25 → T26 ; T6,T25 → T27
T4 → T28
T14,T15 → T29 → T30 → T31 ; T29,T30 → T32
T3,T4,T20,T23,T26,T27,T28 → T33 → T34 → T35 → T36 ; T35 → T38
T12,T15,T32 → T39 → T40 → T41 → T42, T43, T44 → T45 → T46 → T47 → T48 ; T41 → T49
T36,T38,T40,T43 → T37 ; T35 → T63 ; T38 → T64
T41 → T52 → T53 (T50) → T54 (T51) → T55, T56, T57
T56 → T58 → T59 (T48,T49) ; T55 → T60 ; T57,T59,T60 → T61
T37,T38,T42,T61 → T62
```

可并行的独立线：第一组的 T2–T7 互不依赖；计算积木（T16–T18）与网页积木（T21–T23）可以交替做；远程后端 T50、T51 只依赖 T1，可以提前做。

## 开发进度（2026-11-16）

| 组 | 任务 | 状态 | 证据 |
|---|---|---|---|
| 一 准备与底层 | T1–T8 | 完成 | 单测通过；ver0 T09/T11/T12 通过 |
| 二 格式、校验、转换 | T9–T15 | 完成 | `test_taskbook_blocks/book/validate/convert` 通过 |
| 三 计算积木 | T16–T18 | 完成 | `test_taskbook_calc` 通过 |
| 四 引擎与积木 | T19–T27 | 完成 | `test_taskbook_engine/web/pick` 通过 |
| 五 运行器、样例、命令行、退役 | T28–T37 | 完成 | 替身 4b 跑两份夹具与标准答案一致；`python -m hands book` 可用；旧判定已删 |
| 六 验收脚本、任务书服务 | T38–T43、T63、T64 | 完成 | d12 / d17 / d18（CDP）全过；`d12 --replay` 树相同；面板 B01–B07 全过 |
| 七 搭建工具、任务树页 | T44–T49 | 完成 | 面板预览：5 份任务书表单往返完全一致；真实 4b 试跑卡片正常；768 宽无横向滚动 |
| 八 远程后端 | T50–T57（T52 提前在 T41 一起做） | 完成 | `test_remote_devices/access/tunnel` 通过 |
| 九 平板页、电脑卡片 | T58–T61 | 完成 | 面板 R01–R10 全过；预览里局域网真实配对 → /m 看到缩略图、发起试跑 |
| 十 全量回归 | T62 | **暂缓** | 按用户要求：先只做冒烟（单测 323 条、ver0 T01/T09/T12/T16、面板 P01–P03、d17 全过），用户亲自演示后再跑全量 |

### 与文档不一致、开发中补的地方（待用户确认）
1. 「打开第 N 个」加参数 `count`（连续打开几个，子流程产出合并）：点餐要读每家店的详情，原设计只能开一家。
2. 抽列表找不到锚点时：不产出空，而是不按锚点过滤、照常抽，并在记录里写明（原 T26 写的是产出空）。
3. 抽列表把共用的"所在块"按条目切开（`split_contexts`）：夹具页上每条结果的所在块是整个列表，不切开数比例会算错（d12 真实浏览器首跑发现）。
4. 抽列表没有锚点时先回到页面顶部再看（d18 首跑发现上一块把页面留在了底部）。
5. 夹具 `f22_search`：optimus 的相关搜索加了别名「擎天柱」「Optimus Prime」（AC4），相关搜索挪到第一屏之外，加了第 2 页和「下一页」（AC3）。
6. 电脑远程卡片除二维码外加了「复制配对链接」（扫不了码时用；接口只在本机入口）。
7. 已知：面板进程被强杀（不是正常退出）时 cloudflared 子进程可能残留，留到调试阶段。