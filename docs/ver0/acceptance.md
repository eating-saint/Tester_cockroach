# ver0 验收报告

日期：2026-10-29　环境：Windows 11、Python 3.12.14、Chrome 156.0.8078.73（无头）
正式验收产物：`runs/20261029-083041-acceptance/`（日志 `runs/final_acceptance.log`）

## 结论：37/37 通过

## 实现完整性（C1–C22）
- [x] C1 启动/关闭 10 次、无残留 — T01：10/10，关闭后按 profile 查询 chrome.exe 为空
- [x] C2 无头/有界面切换、视口一致 — 两种模式下 F1 视口都是 1249×705（有界面窗口由 `relaunch(headless=False)` 打开）
- [x] C3 崩溃检测与重跑 — T02：检测耗时 0.24s，重跑通过，报告 crashes=1
- [x] C4 F1 数量 — T03：button 5 / textbox 3 / link 8
- [x] C5 F2 可见/可用 — T04：10 个元素全部正确
- [x] C6 F1 位置误差 — T05：16 个元素最大误差 0.00px
- [x] C7 标注图人工核对 — `T05_f01_boxes.png` 已目视检查：16 个框全部与元素对齐，普通文字未被框入
- [x] C8 F3 动态等待 — T06：2516ms 后列表 6 项已出现；源码静态检查无固定 sleep
- [x] C9 F4 超时 — T07：5015ms 返回 timeout，列出 `/api/delay?ms=8000`
- [x] C10 iframe/Shadow — T08：同源、跨源、影子按钮误差均 0.0px，点击结果正确，跨源子会话数 1
- [x] C11 表单（含中文） — T09：读到"你输入了：宫保鸡丁 abc"
- [x] C12 懒加载 — T10：第二页 10 项全部出现
- [x] C13 故障页 — T11：`/missing` 404；console 含 FIXTURE_ERROR_42
- [x] C14 跳转/新标签页/后退 — T12 通过
- [x] C15 reddit 两版本 — T13：事件序列 create(root)→create(n1)→complete(n1)→complete(root)，5 条评论全读到；无 reddit 版写明"未出现"，无点击
- [x] C16 步数上限 — T14：5 步后 force_fail(step_limit)，1.0s 结束
- [x] C17 上下文清理 — T15：子任务 context=None，母任务只含摘要，无 PageState 字段
- [x] C18 报告完整 — T16：每步 pagestate/decision/action/截图/耗时齐全
- [x] C19 决策重问 3 次 — 单元测试 `test_retry_*` 通过；I2 中第一次故意不合法的决策被重问后成功
- [x] C20 未注册工具被拒 — 单元测试 `test_invalid[...hack...]` 通过
- [x] C21 深度上限 — 单元测试 `test_max_depth` 通过
- [x] C22 验证码人工介入 — 假验证码页 `extra/gate.html`：截图已保存 → 切换到有界面窗口（模拟人工在其中完成验证）→ 回到无头 → 用例完成（root=done）

## 集成（I1–I6）
- [x] I1 主循环每步留痕 — T16 / t16_report 用例目录检查通过
- [x] I2 决策器可替换 — 注入一个返回固定 JSON 的假决策器，其他代码不改，用例通过
- [x] I3 跨源 iframe 请求计入网络安静 — F5 导航返回时跨源按钮已可读（T08）；修复了跨会话请求记账问题后验证
- [x] I4 运行目录互不覆盖 — T16：两次运行生成不同目录，第一次的文件修改时间不变
- [x] I5 域名最小间隔 — 同一域名间隔 2s 的配置下，第二次访问等待 2.02s；其他域名不受影响
- [x] I6 专用用户目录 — 所有 Chrome 进程命令行的 `--user-data-dir` 都指向 `profiles/ver0`；残留进程检查也按这个目录筛选

## 编译与测试（B1–B4）
- [x] B1 `pip install -e .` 成功，`import hands` 正常
- [x] B2 `pytest -q`：37 passed
- [x] B3 `python run_tests.py`：T01–T17 全部通过，退出码 0
- [x] B4 依赖仅 websockets、httpx、pillow、pyyaml（测试用 pytest、pytest-asyncio）

## 端到端（E1–E5）
- [x] E1 `python -m hands run cases/t13_reddit_yes.yaml` 通过（5 步），报告 `runs/20261029-084658-2a5y-e1/report.html`
- [x] E2 崩溃恢复（T02）：检测 0.24s → 重启 → 重跑通过
- [x] E3 50 轮稳定性：550/550 通过；Python 内存 57→57MB，Chrome 内存 633→640MB（第 5→50 轮，+1.1%）
- [x] E4 Bing 冒烟：提取到 10 条结果的标题和链接；抽查 5 条，解码跳转链接后真实目标均与标题一致（runoob、realpython、cnblogs、geek-blogs、liaoxuefeng）。本次未遇到验证码。
- [x] E5 有界面复核：`--headed` 运行同一用例，通过且步骤与无头模式一致（5 步）。窗口中的动作过程需要用户自己目视确认。

## 补充：有界面 Bing 演示发现的问题（2026-10-29 中午）
用户观看 `--headed` 演示时发现：程序在搜索结果显示出来之前就读取了页面。第一次演示只读到 1 条页脚链接，但仍然判了"通过"。原因和修复见 plan.md"开发中的设计补充"最后五条：
- 按回车后等待 0ms 就返回 → 安静计时改为从动作开始算起。
- 改完后又暴露出 Bing 的埋点和长连接请求导致等满 30s 超时 → 忽略 Ping 类型请求；load 之后发出、挂满 3s 的请求视为后台长连接，不再阻塞等待。
- T20 判定太宽 → 新增 `summary_count_min`，要求至少 5 条结果链接。

修复后的验证：
- `pytest -q`：37 passed。
- `python run_tests.py`：17/17 通过，T17 共 550/550（产物在 `runs/20261029-115621-acceptance/`）。
- Bing 有界面和无头各跑一次都通过：按回车后约 3.5s 稳定，读到 10 条结果，读取时的截图里结果已经显示出来。
- 代价：每个动作后至少等 0.5s，T17 50 轮总耗时随之变长。

## 需要用户知道的事项
1. Bing 结果链接都是 `bing.com/ck/a` 跳转，T20 用例改为只取这类链接，并新增可选参数 `title_min_len` 过滤导航项。末尾仍可能混入一两个页脚链接（如"隐私声明和 Cookie"）。
2. E5 和 C22 的"有界面窗口"由程序自动打开。当时用户不在场，窗口内的画面没有人看过，结论依据的是运行结果。
3. 开发中的设计补充已记录在 plan.md 的"开发中的设计补充"一节。
4. 项目目录不是 git 仓库，所以没有按 task 提交代码。
