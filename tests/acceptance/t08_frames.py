"""T08 M3 iframe / Shadow DOM（F5、F6）：同源、跨源 iframe 和 Shadow DOM 内按钮都能读到、位置准确、能点中；
跨源 iframe 确实运行在独立会话（独立进程）中。"""
from hands.actions import ActionExecutor

from common import box_diff, load_expected, read_elements

RECT = "(()=>{const r=%s.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})()"


async def run(ctx):
    page = await ctx.page()
    ax = ActionExecutor(page)
    notes = []
    # F5
    await page.navigate(ctx.url("f05_iframe/index.html"))
    child = [f for f in page.sessions() if f.parent is not None]
    if not child:
        return False, "跨源 iframe 没有独立会话（未运行在独立进程）"
    await page.refresh_offsets()
    for b in load_expected("f05_iframe")["buttons"]:
        els, _ = await read_elements(page)
        e = next((x for x in els if x.name == b["name"]), None)
        if e is None:
            return False, f"未读到 {b['name']}"
        if b["cross_origin"]:
            fs = page.frame_of(e._ref.session_id)
            if fs.parent is None:
                return False, f"{b['name']} 不在跨源子会话中"
            r = await page.evaluate(RECT % f"document.getElementById('{b['dom_id']}')", fs.session)
            ox, oy = fs.owner_offset
            real = [r[0] + ox, r[1] + oy, r[2], r[3]]
        else:
            real = await page.evaluate(
                "(()=>{const f=document.getElementById('%s');const fr=f.getBoundingClientRect();const cs=getComputedStyle(f);"
                "const r=f.contentDocument.getElementById('%s').getBoundingClientRect();"
                "const bx=parseFloat(cs.borderLeftWidth)+parseFloat(cs.paddingLeft),by=parseFloat(cs.borderTopWidth)+parseFloat(cs.paddingTop);"
                "return [fr.x+bx+r.x,fr.y+by+r.y,r.width,r.height]})()" % (b["iframe_id"], b["dom_id"]))
        d = box_diff(e.box, real)
        if d > 1:
            return False, f"{b['name']} 位置误差 {d:.2f}px"
        r = await ax.click(e)
        got = await page.evaluate("document.getElementById('result').textContent")
        if not r.ok or got != b["result"]:
            return False, f"点击 {b['name']} 结果 {got!r}（{r.error}）"
        notes.append(f"{b['name']} 误差{d:.1f}px")
    # F6
    await page.navigate(ctx.url("f06_shadow/index.html"))
    els, _ = await read_elements(page)
    b = load_expected("f06_shadow")["buttons"][0]
    e = next((x for x in els if x.name == b["name"]), None)
    if e is None or not e.in_shadow:
        return False, "未读到影子按钮或 in_shadow 标记错误"
    real = await page.evaluate(RECT % "document.querySelector('x-panel').shadowRoot.getElementById('btn-shadow')")
    d = box_diff(e.box, real)
    if d > 1:
        return False, f"影子按钮位置误差 {d:.2f}px"
    r = await ax.click(e)
    got = await page.evaluate("document.getElementById('result').textContent")
    if got != b["result"]:
        return False, f"点击影子按钮结果 {got!r}"
    notes.append(f"影子按钮 误差{d:.1f}px")
    return True, "；".join(notes) + f"；跨源子会话数 {len(child)}"
