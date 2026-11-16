# 桌面驱动先行验证结论

日期：2026-11-14　环境：Windows 11、Chrome 156（默认浏览器 ChromeHTML）、系统缩放 100%（DPI 96）、Python 3.12、comtypes 1.5.0
脚本：`tools/probe_desktop.py --item all`（原始结果 `runs/probe/probe.json`、截图 `runs/probe/printwindow*.png`）

| # | 风险点 | 实测 | 结论 |
|---|---|---|---|
| 1 | Chrome 在 UIA 请求下开启无障碍树、读取耗时 | 新窗口打开 f01 后 0.02 s 找到 Document；`FindAllBuildCache` 一次取 31 个元素，耗时 16 ms（之后 < 1 ms）；角色、名称、位置框、AriaRole 齐全；视口外元素也在树里（位置框在 Document 之外） | **主方案可用**，不需要用户手动打开无障碍开关 |
| 2 | Document 的 ScrollPattern | 滚 3 格前后 `VerticalScrollPercent` 0 → 23.27，`VerticalViewSize` 37.4 | **主方案可用**：`scroll_y = percent/100 × (content_h − vh)`，`content_h = vh × 100 / ViewSize` |
| 3 | 链接地址 | Hyperlink 的 `ValuePattern.Value` 与 `LegacyIAccessible.Value` 都是带协议的完整地址 | **主方案可用**（用 ValuePattern） |
| 4 | PrintWindow(PW_RENDERFULLCONTENT) | 非黑像素 100%；红色置顶窗口压在内容区上时，截图里红色占比 0% | **主方案可用**；同时满足 N5（不截到别的窗口） |
| 5 | 低级钩子读回注入标记 | 5 次注入移动全部读到 `dwExtraInfo = INJECT_MARK`，且带 `LLMHF_INJECTED` | **主方案可用**；人工输入一侧（无标记）在 d08 由用户亲手验证 |
| 6 | 浏览器界面控件 | `OmniboxViewViews`（名称"地址和搜索栏"）、`ReloadButton`（"重新加载"）、`TabStrip`、`Tab` 都能按类名找到；但见下方两处差异 | **部分改用备选** |

## 与 plan 不同的地方（实现按这里做）

1. **网址不从地址栏读**：地址栏的值省略了 `http://`（如 `127.0.0.1:8799/f01_static/index.html`）。改为读 **Document 控件的 ValuePattern.Value**，实测是完整网址。→ 影响 T19、T22。
2. **没有"正在加载"信号**：导航到一个 5 秒才响应的地址期间，重新加载按钮的名称、Legacy 状态位、标签名称都不变（Chrome 156 中文界面）。改用：
   - 导航后先等 **Document 的网址变为目标地址**（新文档已提交），再等截图稳定；
   - 点击等动作后：短时间内（1 秒）观察网址是否变化，变了就按导航处理，没变就只等截图稳定。
   - 夹具 `f20_desktop` 的"慢加载页"做成服务端慢响应（导航本身慢），而不是页面脚本延时渲染——后者桌面驱动无从得知，记为已知差异。
   → 影响 T16、T24、T33。
3. **页面标题用窗口标题**：标签控件的名称带悬停卡片文字（"F1 静态页 - 内存用量 - 329 MB"），标题改为窗口标题去掉" - Google Chrome"。→ 影响 T22。
4. **网页元素的 ClassName 是 HTML 的 class 属性**（如 `a`），不能拿来区分浏览器界面与网页内容；区分一律以"是否在 Document 子树内"为准。→ 影响 T18、T19。
5. **用户默认 Profile 的扩展会往页面里注入元素**：实测看到沉浸式翻译扩展的浮层（class `imt-fb-container`）在 Document 子树里。它们会作为普通元素进入元素清单；本章不做过滤（属于页面真实内容），在验收中如果干扰选号再处理。

## 结论
6 项中 5 项按主方案，第 6 项按上面 1–4 条调整。没有阻塞问题，按 task.md 继续。
