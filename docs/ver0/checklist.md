# ver0 无头版"手脚" Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。括号内为验证方式。
> 自动验收命令：`python run_tests.py`；单元测试：`pytest -q`；单用例：`python -m hands run <用例>`。

## 实现完整性

- [x] C1 浏览器可启动、连接、关闭，10 次循环全部成功且无残留进程（AC1；`run_tests --only T01` 通过，结束后按专用用户目录查询 chrome.exe 进程为空）
- [x] C2 默认无头运行，加 `--headed` 能看到浏览器窗口，两种模式视口尺寸相同（运行任一用例两次，对比 PageState 中 viewport，人工看到窗口出现）
- [x] C3 强制结束 Chrome 后 5 秒内检测到，自动重启，当前用例从头重跑并通过，报告记 1 次崩溃（AC2；`run_tests --only T02` 通过）
- [x] C4 F1 静态页按钮/输入框/链接数量与标准答案一致（AC3；`--only T03`）
- [x] C5 F2 每个元素的可见、可用标记正确（AC4；`--only T04`）
- [x] C6 F1 元素位置框与 getBoundingClientRect 误差 ≤ 1 像素（AC5；`--only T05`）
- [x] C7 F1 元素标注图人工核对，框与元素无错位（AC5；打开 T05 结果中给出的标注图路径）
- [x] C8 F3 等待返回时列表已出现，等待耗时 ≥ 2 秒，等待逻辑中无固定 sleep（AC6；`--only T06`）
- [x] C9 F4 超时 5 秒时约 5 秒返回"超时"（误差 1 秒内），并列出慢请求地址（AC7；`--only T07`）
- [x] C10 F5 同源、跨源 iframe 与 F6 Shadow DOM 内的按钮都能读到，位置误差 ≤ 1 像素，点击结果与标准答案一致，且跨源 iframe 有独立会话（AC8；`--only T08`）
- [x] C11 F7 表单输入 → 提交 → 结果文字与输入一致，含中文（AC9；`--only T09`）
- [x] C12 F8 滚到底后第二页内容出现在元素列表（AC10；`--only T10`）
- [x] C13 F9 的 404 被记为失败请求且状态码 404，console 报错被记录且内容一致（AC11；`--only T11`）
- [x] C14 F11 同页跳转与新标签页都能跟随，网址正确，后退回到原页面（AC12；`--only T12`）
- [x] C15 F10 有 reddit：任务树 母任务 → 子任务，子任务完成，5 条评论全部读到；无 reddit：不创建子任务或写明"未出现"，且无任何点击（AC13；`--only T13`）
- [x] C16 永远达不成的规则在步数上限处被强制失败，程序在时限内正常结束（AC14；`--only T14`）
- [x] C17 子任务结束后母任务上下文只含摘要、不含子任务 PageState（AC15；`--only T15`）
- [x] C18 每条用例都有通过/失败、每步 PageState 与决策 JSON、截图、耗时，PageState 能完整解析为 JSON（AC16；`--only T16`）
- [x] C19 决策不合法时最多重问 3 次，三次都不合法则该节点失败而非程序崩溃（F8；`pytest -q` 中决策校验相关用例通过）
- [x] C20 只能调用已注册工具，未注册工具名被拒绝（F8；`pytest -q` 中决策校验相关用例通过）
- [x] C21 树深度超上限时创建子任务被拒绝，不新建节点（F9；`pytest -q` 中任务树相关用例通过）
- [x] C22 遇到验证码页时进入"需要人工"：保存截图、切换为有界面打开同一网址、命令行提示，回车后回到无头继续执行（F12；对假验证码页运行用例，观察窗口弹出与提示，回车后用例继续直到结束）

## 集成

- [x] I1 用例主循环按 检查 → 决策 → 执行 → 记录 的顺序运行，每一步都在运行目录留下 PageState、决策、动作结果、截图（运行 t16 用例，查看每个步骤目录文件齐全）
- [x] I2 决策器可替换：用一个返回固定 JSON 的假决策器替换规则决策器，用例仍能跑完且其他代码不改（`pytest -q` 中假决策器用例通过，或临时脚本注入 decider_factory 运行 F7 用例通过）
- [x] I3 跨源 iframe 内的网络请求也计入"网络安静"判断（打开 F5，导航返回时跨源 iframe 内按钮已可读到；监控状态中能看到 localhost 的请求记录）
- [x] I4 每次运行产物在独立目录，两次运行互不覆盖（AC20；连续运行同一用例两次，runs 下出现两个不同目录，第一个目录文件修改时间不变）
- [x] I5 访问同一外部域名时遵守最小间隔（连续两次运行 Bing 用例，日志显示第二次导航前等待至间隔满足）
- [x] I6 浏览器只使用项目专用用户目录，不影响日常 Chrome（运行期间日常 Chrome 照常可用；进程命令行中 user-data-dir 指向项目 profiles 目录）

## 编译与测试

- [x] B1 `pip install -e .` 成功，`python -c "import hands"` 无报错
- [x] B2 `pytest -q` 单元测试全部通过
- [x] B3 `python run_tests.py` 输出 编号 / 通过或失败 / 耗时 / 失败原因 表，T01–T17 全部通过，退出码 0（AC19）
- [x] B4 依赖仅限 websockets、httpx、pillow、pyyaml、pytest（及 pytest-asyncio）（查看 pyproject.toml 依赖列表与 `pip install -e .` 输出）

## 端到端场景

- [x] E1 有 reddit 的假搜索：`python -m hands run cases/t13_reddit_yes.yaml` → 打开 report.html，看到用例通过，步骤依次为 建子任务 → 点击 reddit 链接 → 读取评论 → 完成子任务 → 完成母任务，最终摘要含 5 条评论关键词，每步有截图
- [x] E2 浏览器中途崩溃：运行 T02 → 观察日志出现崩溃检测与重启，用例最终通过，报告中崩溃次数为 1
- [x] E3 稳定性：`python run_tests.py --only T17`（50 轮）→ 通过率 100%，第 50 轮 Python 与 Chrome 内存较第 5 轮增长均不超过 20%（AC17）
- [x] E4 Bing 冒烟：`python -m hands run cases/t20_bing.yaml` → 报告中读取到非空的"标题 + 链接"列表，人工抽查 5 条与 Bing 页面一致；如遇验证码，弹出有界面窗口与提示，人工处理并回车后用例继续（AC18）
- [x] E5 有界面复核：`python -m hands run cases/t13_reddit_yes.yaml --headed` → 窗口中可见点击与跳转过程，结果与无头模式一致
