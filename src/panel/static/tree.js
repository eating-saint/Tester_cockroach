// 任务树页：任务书运行中的树、暂停后改树（带版本号，两端同时改时后到的被拒）、再给 N 次、结束并输出。
// 电脑面板与平板共用（平板经远程入口打开，没有面板令牌，靠设备凭证 cookie）。
"use strict";
const TOKEN = (document.querySelector('meta[name="panel-token"]') || {}).content || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const STATE = { idle: "未运行", running: "运行中", waiting: "等待决策", done: "已完成", stopped: "已停止" };
const WAIT = { budget: "次数用满", decision: "任务书要求交给人" };
const ORIGIN = { initial: "初", model: "模", rule: "规", user: "人" };
const PAUSE = { human_input: "检测到你在操作鼠标键盘，已暂停", user: "已暂停", budget: "次数用满，等待决策",
  decision: "任务书要求交给人，等待决策", handoff: "需要人工处理（到电脑前）" };
const EMBED = window.self !== window.top;      // 嵌在平板页里
let books = [], snap = null, sel = null, st = null, preview = null;

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (TOKEN) opt.headers["X-Panel-Token"] = TOKEN;
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/pair"; throw new Error("需要先配对"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) { const e = new Error(j.error || r.statusText); e.status = r.status; throw e; }
  return j.data;
}
function msg(t, bad) { $("msg").textContent = t; $("msg").className = bad ? "warn" : "muted"; }
if (EMBED) { $("homeLink").classList.add("hidden"); document.body.classList.add("embed"); }

// ---------------- 任务书（没在跑时看初始形态、发起） ----------------
async function loadBooks() {
  books = await api("GET", "/api/books");
  const keep = $("bookSel").value;
  $("bookSel").innerHTML = books.map((b) => `<option value="${esc(b.name)}">${esc(b.title)}${b.status !== "ok" ? "（不能运行）" : ""}</option>`).join("");
  if (keep && books.some((b) => b.name === keep)) $("bookSel").value = keep;
  await loadPreview();
}
async function loadPreview() {
  const b = books.find((x) => x.name === $("bookSel").value);
  preview = null;
  $("buildLink").href = b ? `/build?name=${encodeURIComponent(b.name)}` : "/build";
  if (!b) return render();
  try {
    const g = await api("GET", `/api/books/${encodeURIComponent(b.name)}`);
    preview = { book: g.book, ...(await api("POST", "/api/books/preview", { text: g.text })) };
  } catch (e) { preview = { error: e.message }; }
  render();
}
async function start(trial) {
  try {
    await api("POST", "/api/books/run", { name: $("bookSel").value, budget: +$("budget").value || null, trial });
    msg(trial ? "试跑已开始" : "已开始"); sel = null; poll();
  } catch (e) { msg(e.message, true); }
}
$("bookSel").addEventListener("change", loadPreview);
$("bRun").addEventListener("click", () => start(false));
$("bTrial").addEventListener("click", () => start(true));

// ---------------- 改树（带 rev） ----------------
async function edit(op, extra) {
  try {
    await api("POST", "/api/expand/edit", { op, rev: snap && snap.rev, ...extra });
    msg("已修改");
  } catch (e) {
    msg(e.status === 409 && /另一端/.test(e.message) ? "已被另一端修改，请刷新（已自动刷新）" : e.message, true);
  }
  poll();
}
const post = (path, body) => api("POST", path, body).then(poll).catch((e) => msg(e.message, true));
$("bPause").addEventListener("click", () => post("/api/jobs/pause"));
$("bResume").addEventListener("click", () => post("/api/jobs/resume"));
$("bFinish").addEventListener("click", () => edit("finish"));
$("bGrant").addEventListener("click", () => edit("grant", { n: +$("grantN").value || 0 }));
$("bDel").addEventListener("click", () => sel && edit("delete", { node: sel }));
$("bTerm").addEventListener("click", () => sel && edit("set_terminal", { node: sel }));
$("bPend").addEventListener("click", () => sel && edit("set_pending", { node: sel }));
$("bGroup").addEventListener("click", () => edit("add_group", { key: $("gKey").value.trim(),
  values: $("gVals").value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean) }));
$("bAllow").addEventListener("click", () => post("/api/jobs/confirm", { allow: true }));
$("bDeny").addEventListener("click", () => post("/api/jobs/confirm", { allow: false }));

// ---------------- 画树 ----------------
function treeHtml(nodes, current) {
  const by = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const li = (n) => `<li><span class="node s-${n.state}${n.id === current ? " cur" : ""}${n.id === sel ? " sel" : ""}" data-id="${n.id}">` +
    `<span class="dot"></span>${esc(n.word)}<span class="origin">${ORIGIN[n.origin] || ""}${n.searches ? " · 查 " + n.searches : ""}</span></span>` +
    (n.children.length ? `<ul class="tree">${n.children.map((c) => li(by[c])).join("")}</ul>` : "") + "</li>";
  return `<ul class="tree">${nodes.filter((n) => !n.parent).map(li).join("")}</ul>`;
}
function renderPreview() {
  if (!preview) { $("tree").innerHTML = "（还没有任务书）"; return; }
  if (preview.error) { $("tree").innerHTML = `<span class="warn">${esc(preview.error)}</span>`; return; }
  const b = preview.book || {};
  const order = (preview.order || []).map((x) => esc(x.label)).join(" → ");
  const groups = (b.groups || []).map((g) => { const [k, v] = Object.entries(g)[0]; return `<li>${esc(k)}：${esc(v.join("、"))}</li>`; }).join("");
  $("tree").className = "";
  $("tree").innerHTML = `<p class="muted small">初始形态（未运行，不联网）：不加任何新节点时要处理 ${(preview.order || []).length} 个节点。</p>` +
    `<p class="small"><b>初始组</b></p><ul class="small">${groups}</ul>` +
    `<p class="small"><b>处理顺序</b>：${order}</p>` + (preview.final && preview.final.nodes ? treeHtml(preview.final.nodes, null) : "");
}
function renderTree() {
  if (!snap || !snap.nodes) { renderPreview(); return; }
  $("tree").className = "";
  $("tree").innerHTML = treeHtml(snap.nodes, snap.current);
  document.querySelectorAll("#tree .node[data-id]").forEach((el) => el.addEventListener("click", () => {
    sel = sel === el.dataset.id ? null : el.dataset.id; renderTree(); renderDetail();
  }));
}
function runDir() { return (snap && snap.run) || ""; }
function renderDetail() {
  const n = snap && snap.nodes && snap.nodes.find((x) => x.id === sel);
  for (const id of ["bDel", "bTerm", "bPend"]) $(id).disabled = !n;
  document.body.classList.toggle("has-sel", !!n);
  if (!n) { $("detail").className = "muted"; $("detail").textContent = "点树上的节点查看"; return; }
  const reasons = (n.reasons || []).map((r) => `<li>[${esc(r.actor)}] ${esc(r.op || "")}${r.step ? " ← " + esc(r.step) : ""}` +
    `${r.reason ? "：" + esc(r.reason) : ""}${r.word ? "「" + esc(r.word) + "」" : ""}` +
    (Array.isArray(r.evidence) && r.evidence.length ? `（依据：${esc(r.evidence.slice(0, 8).join("、"))}）` : "") + "</li>").join("");
  $("detail").className = "";
  $("detail").innerHTML = `<b>${esc(n.word)}</b>（${esc(n.id)}）状态 ${esc(n.state)}，来源 ${esc(n.origin)}，处理 ${n.searches} 次<br>` +
    (n.url ? `网址：<span class="small">${esc(n.url)}</span><br>` : "") +
    (n.shot && runDir() ? `<a href="/runs/${esc(runDir())}/${esc(n.shot.replace(/\\/g, "/"))}" target="_blank">截图</a><br>` : "") +
    (reasons ? `<ul class="small">${reasons}</ul>` : "");
}
function renderChart() {
  const s = (snap && snap.series) || [];
  const svg = $("chart");
  if (s.length < 2) { svg.innerHTML = ""; return; }
  const max = Math.max(1, ...s.map((p) => Math.max(p.leaves, p.nodes, p.used)));
  const line = (k, cls) => `<polyline fill="none" stroke-width="2" stroke="${cls}" points="${s.map((p, i) =>
    `${(i / (s.length - 1)) * 320},${136 - (p[k] / max) * 130}`).join(" ")}"/>`;
  svg.innerHTML = line("leaves", "#2a9d5b") + line("nodes", "#2f6fd6") + line("used", "#b26a00");
}
function renderResult() {
  const r = snap && snap.result;
  if (!r) { $("result").className = "muted"; $("result").textContent = "（任务结束后显示）"; return; }
  let h = `<p>末端叶子 ${r.leaves.length} 个${r.trial ? "（试跑）" : ""}</p>`;
  const rk = r.ranked;
  if (rk) {
    const fields = (rk.sort || []).map((s) => s.split(":")[0]);
    for (const [g, rows] of Object.entries(rk.top)) {
      h += `<b>${esc(g || "结果")}</b><table><tr><th>#</th><th>名称</th>${fields.map((f) => `<th>${esc(f)}</th>`).join("")}</tr>` +
        rows.map((x, i) => `<tr><td>${i + 1}</td><td>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.word)}</a>` : esc(x.word)}</td>` +
          fields.map((f) => `<td>${esc((x.fields || {})[f] || "未知")}</td>`).join("") + "</tr>").join("") + "</table>";
    }
    if (rk.unknown && rk.unknown.length)
      h += `<p class="warn small">字段未知（${rk.mode === "exclude" ? "未参与排序" : "排在已知之后"}）：${rk.unknown.map((x) => esc(x.word)).join("、")}</p>`;
  } else {
    h += "<ul>" + r.leaves.map((l) => `<li>${esc(l.key || "")} / ${esc(l.word)}${l.step ? ` <span class="muted small">← ${esc(l.step)}</span>` : ""}</li>`).join("") + "</ul>";
  }
  if (r.run) h += `<p class="small"><a href="/runs/${esc(r.run)}/book/report.html" target="_blank">报告页</a></p>`;
  $("result").className = ""; $("result").innerHTML = h;
}
function render() {
  const c = (snap && snap.counts) || {};
  const running = !!(st && st.job && st.job.kind === "book" && st.busy);
  const b = snap && snap.book;
  $("title").textContent = b ? `任务树：${b.title || b.name}${snap.trial ? "（试跑）" : ""}` : "任务树";
  $("state").textContent = snap ? (STATE[snap.state] || snap.state) + (snap.wait_reason ? `（${WAIT[snap.wait_reason] || snap.wait_reason}）` : "") : "未运行";
  $("used").textContent = c.used ?? 0; $("budgetNow").textContent = c.budget ?? 0;
  $("nNodes").textContent = c.nodes ?? 0; $("nLeaves").textContent = c.leaves ?? 0; $("nPending").textContent = (c.pending ?? 0) + (c.tentative ?? 0);
  const p = st && st.pause;
  $("pauseText").textContent = p ? (PAUSE[p.reason] || p.reason) : "";
  const busy = !!(st && st.busy);
  $("bRun").disabled = $("bTrial").disabled = busy;
  $("bookSel").disabled = busy;
  $("bPause").disabled = !running || !!p;
  $("bResume").disabled = !p || p.reason === "budget" || p.reason === "decision" || p.reason === "handoff";
  $("bFinish").disabled = $("bGrant").disabled = !running;
  const conf = st && st.confirm;
  $("confirmBar").classList.toggle("hidden", !conf);
  if (conf) $("confirmText").textContent = conf.text || "";
  renderTree(); renderDetail(); renderChart(); renderResult();
}
async function getState() {      // /api/state 直接返回状态对象（不包 ok / data）
  const r = await fetch("/api/state", { headers: TOKEN ? { "X-Panel-Token": TOKEN } : {} });
  return r.ok ? r.json() : null;
}
async function poll() {
  try {
    [snap, st] = await Promise.all([api("GET", "/api/expand/tree"), getState().catch(() => null)]);
  } catch (e) { return; }
  render();
}
loadBooks().catch((e) => msg(e.message, true));
poll();
setInterval(poll, 1500);
