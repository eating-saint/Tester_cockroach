// 任务树页：发起关联展开任务、查看树与变化曲线、暂停后改树（spec F15、F16；下一章手机复用这些接口）
const TOKEN = document.querySelector('meta[name="panel-token"]').content;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const STATE = { idle: "未运行", running: "运行中", waiting: "等待决策（次数用满）", done: "已完成", stopped: "已停止" };
const ORIGIN = { initial: "初", model: "模", user: "人" };
const PAUSE = { human_input: "检测到你在操作鼠标键盘，已暂停", user: "已暂停", budget: "次数用满，等待决策", handoff: "人工处理中" };
let types = [], snap = null, sel = null, st = null;

async function api(method, path, body) {
  const opt = { method, headers: { "X-Panel-Token": TOKEN } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || r.statusText);
  return j.data;
}
function msg(t, bad) { $("msg").textContent = t; $("msg").className = bad ? "warn" : "muted"; }

async function loadTypes() {
  types = await api("GET", "/api/expand/types");
  $("typeSel").innerHTML = types.map((t, i) => `<option value="${i}">${esc(t.title)}${t.error ? "（定义有误）" : ""}</option>`).join("");
  renderParams();
}
function renderParams() {
  const t = types[+$("typeSel").value];
  if (!t) return;
  $("paramBox").innerHTML = (t.params || []).map((p) =>
    `<label>${esc(p.label || p.name)} <input data-p="${esc(p.name)}" value="${esc(p.default ?? "")}" size="8"></label>`).join(" ");
  $("budget").value = t.budget || "";
  if (t.error) msg(t.error, true);
}
$("typeSel").addEventListener("change", () => { renderParams(); if (!snap || !snap.nodes) renderPreview(); });
$("bStart").addEventListener("click", async () => {
  const t = types[+$("typeSel").value];
  const params = {};
  document.querySelectorAll("#paramBox [data-p]").forEach((el) => { params[el.dataset.p] = el.value; });
  try { await api("POST", "/api/expand/start", { type: t.file, params, budget: +$("budget").value || null }); msg("已开始"); sel = null; }
  catch (e) { msg(e.message, true); }
});

function edit(op, extra) {
  return api("POST", "/api/expand/edit", { op, ...extra }).then(() => { msg("已修改"); poll(); }).catch((e) => msg(e.message, true));
}
$("bPause").addEventListener("click", () => api("POST", "/api/jobs/pause").then(poll).catch((e) => msg(e.message, true)));
$("bResume").addEventListener("click", () => api("POST", "/api/jobs/resume").then(poll).catch((e) => msg(e.message, true)));
$("bFinish").addEventListener("click", () => edit("finish"));
$("bGrant").addEventListener("click", () => edit("grant", { n: +$("grantN").value || 0 }));
$("bDel").addEventListener("click", () => sel && edit("delete", { node: sel }));
$("bTerm").addEventListener("click", () => sel && edit("set_terminal", { node: sel }));
$("bPend").addEventListener("click", () => sel && edit("set_pending", { node: sel }));
$("bGroup").addEventListener("click", () => edit("add_group", { key: $("gKey").value.trim(),
  values: $("gVals").value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean) }));
$("bAllow").addEventListener("click", () => api("POST", "/api/jobs/confirm", { allow: true }).then(poll));
$("bDeny").addEventListener("click", () => api("POST", "/api/jobs/confirm", { allow: false }).then(poll));

// 没在运行时：按定义画出初始形态（起始词 + 各组的键；值是键就分叉，祖先上已有的键不再展开；已展开过的键不再单独当根）
function renderPreview() {
  const t = types[+$("typeSel").value];
  if (!t || !t.groups) { $("tree").innerHTML = "（还没有运行）"; return; }
  const map = Object.fromEntries(t.groups.map(([k, v]) => [k, v]));
  const expanded = new Set();
  let searches = 0;
  const li = (w, anc) => {
    searches++;
    const isKey = map[w] && !anc.includes(w);
    if (isKey) expanded.add(w);
    const kids = isKey ? map[w].map((v) => li(v, [...anc, w])).join("") : "";
    return `<li><span class="node s-${isKey ? "key" : "pending"}"><span class="dot"></span>${esc(w)}` +
      `<span class="origin">${isKey ? "键" : "待查"}</span></span>${kids ? `<ul class="tree">${kids}</ul>` : ""}</li>`;
  };
  const roots = [t.start, ...t.groups.map(([k]) => k).filter((k) => k !== t.start)];
  let html = "";
  for (const r of roots) {
    if (expanded.has(r)) { html += `<li class="muted small">（${esc(r)} 已在上面展开过，不再单独作为根）</li>`; continue; }
    html += li(r, []);
  }
  const conds = (t.conditions || []).map((c) => `<li>${esc(c.id)}：${esc(c.text)}</li>`).join("");
  $("tree").className = "";
  $("tree").innerHTML = `<p class="muted small">初始形态（未运行）：不加任何新节点时要搜 ${searches} 次；运行中 4b 会按下面的关联条件加节点。<br>` +
    `搜索：${esc(t.search_url)}　搜索词：${esc(t.query)}</p><ul class="tree">${html}</ul>` +
    (conds ? `<p class="small"><b>关联条件</b></p><ul class="small">${conds}</ul>` : "");
}

function renderTree() {
  if (!snap || !snap.nodes) { renderPreview(); return; }
  const by = Object.fromEntries(snap.nodes.map((n) => [n.id, n]));
  const roots = snap.nodes.filter((n) => !n.parent);
  const li = (n) => `<li><span class="node s-${n.state}${n.id === snap.current ? " cur" : ""}${n.id === sel ? " sel" : ""}" data-id="${n.id}">` +
    `<span class="dot"></span>${esc(n.word)}<span class="origin">${ORIGIN[n.origin] || ""}${n.searches ? " · 搜 " + n.searches : ""}</span></span>` +
    (n.children.length ? `<ul class="tree">${n.children.map((c) => li(by[c])).join("")}</ul>` : "") + "</li>";
  $("tree").innerHTML = `<ul class="tree">${roots.map(li).join("")}</ul>`;
  $("tree").className = "";
  document.querySelectorAll(".node").forEach((el) => el.addEventListener("click", () => { sel = el.dataset.id; renderTree(); renderDetail(); }));
}
function renderDetail() {
  const n = snap && snap.nodes && snap.nodes.find((x) => x.id === sel);
  for (const id of ["bDel", "bTerm", "bPend"]) $(id).disabled = !n;
  if (!n) { $("detail").textContent = "点树上的节点查看"; return; }
  const fields = Object.entries(n.fields || {}).map(([k, v]) => `${esc(k)}：${esc(v.value)}${v.grounded ? "" : "（无依据）"}`).join("；");
  const reasons = (n.reasons || []).map((r) => `<li>[${esc(r.actor)}] ${esc(r.op)}：${esc(r.reason || "")}` +
    (r.evidence ? `（依据：${esc(r.evidence.value)}）` : "") + "</li>").join("");
  $("detail").className = "";
  $("detail").innerHTML = `<b>${esc(n.word)}</b>（${esc(n.id)}）状态 ${esc(n.state)}，来源 ${esc(n.origin)}，搜索 ${n.searches} 次<br>` +
    (n.url ? `网址：<span class="small">${esc(n.url)}</span><br>` : "") +
    (n.shot ? `<a href="/runs/${esc(snapRun())}/${esc(n.shot)}" target="_blank">截图</a><br>` : "") +
    (fields ? `字段：${fields}<br>` : "") + (reasons ? `<ul class="small">${reasons}</ul>` : "");
}
function snapRun() { return (st && st.job && st.job.result && st.job.result.dir || "").split("/")[0] || (snap && snap.run) || ""; }
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
  if (!r) return;
  let h = `<p>末端叶子 ${r.leaves.length} 个</p>`;
  const top = r.rank && r.rank.top;
  if (top) for (const [g, rows] of Object.entries(top)) {
    h += `<b>${esc(g || "结果")}</b><table><tr><th>#</th><th>名称</th><th>价位</th><th>评分</th></tr>` +
      rows.map((x, i) => `<tr><td>${i + 1}</td><td>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}</a>` : esc(x.name)}</td>` +
        `<td>${esc(x.price_text || "未知")}</td><td>${esc(x.rating_text || "-")}</td></tr>`).join("") + "</table>";
  }
  if (r.rank && r.rank.ungrounded && r.rank.ungrounded.length)
    h += `<p class="warn small">无依据（未参与排序）：${r.rank.ungrounded.map((x) => esc(x.name)).join("、")}</p>`;
  if (!top) h += "<ul>" + r.leaves.map((l) => `<li>${esc(l.key || "")} / ${esc(l.word)}</li>`).join("") + "</ul>";
  $("result").className = ""; $("result").innerHTML = h;
}
async function poll() {
  try {
    [snap, st] = await Promise.all([api("GET", "/api/expand/tree"), fetch("/api/state", { headers: { "X-Panel-Token": TOKEN } }).then((r) => r.json())]);
  } catch (e) { return; }
  const c = (snap && snap.counts) || {};
  const running = st && st.job && st.job.kind === "expand" && st.busy;
  $("state").textContent = snap ? (STATE[snap.state] || snap.state) : "未运行";
  $("used").textContent = c.used ?? 0; $("budgetNow").textContent = c.budget ?? 0;
  $("nNodes").textContent = c.nodes ?? 0; $("nLeaves").textContent = c.leaves ?? 0; $("nPending").textContent = (c.pending ?? 0) + (c.tentative ?? 0);
  const p = st && st.pause;
  $("pauseText").textContent = p ? (PAUSE[p.reason] || p.reason) : "";
  $("bStart").disabled = !!(st && st.busy);
  $("bPause").disabled = !running || !!p;
  $("bResume").disabled = !p || p.reason === "budget";
  $("bFinish").disabled = $("bGrant").disabled = !running;
  const conf = st && st.confirm;
  $("confirmBar").classList.toggle("hidden", !conf);
  if (conf) $("confirmText").textContent = conf.text || "";
  renderTree(); renderDetail(); renderChart(); renderResult();
}
loadTypes().catch((e) => msg(e.message, true));
poll();
setInterval(poll, 1500);
