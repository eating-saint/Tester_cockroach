// 平板外壳：状态（缩略画面、待确认、暂停 / 恢复 / 取消）、任务树、任务书（选、发起、搭建）、记录。
// 远程入口没有面板令牌，靠设备凭证 cookie；断线每 2 秒重连。待确认 / 需要人工 / 次数用完时醒目提示并震动、响一声。
"use strict";
const TOKEN = (document.querySelector('meta[name="panel-token"]') || {}).content || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const JOB_STATE = { running: "运行中", need_human: "需要人工", stopping: "停止中", done: "完成", failed: "失败", stopped: "已停止", need_confirm: "等你确认" };
const PAUSE = { human_input: "检测到电脑前有人在操作鼠标键盘，已暂停", user: "已暂停", budget: "次数用完，等待决策（到“任务树”里再给 N 次或结束并输出）",
  decision: "任务书要求交给人，等待决策（到“任务树”里处理）", handoff: "需要人工处理：请到电脑前" };
let S = null, ws = null, lastAlert = "", audio = null, shotUrl = null, books = [];

// ---------------- 接口 ----------------
function headers(json) {
  const h = {};
  if (TOKEN) h["X-Panel-Token"] = TOKEN;
  if (json) h["Content-Type"] = "application/json";
  return h;
}
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: headers(body !== undefined), body: body !== undefined ? JSON.stringify(body) : undefined });
  if (r.status === 401) { location.href = "/pair?need=1"; throw new Error("需要先配对"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) throw new Error(j.error || r.statusText);
  return j.data !== undefined ? j.data : j;
}
const act = (path, body) => api("POST", path, body).catch((e) => toast(e.message));
function toast(t) { $("jobLine").textContent = t; }

// ---------------- 实时连接 ----------------
function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${scheme}://${location.host}/ws${TOKEN ? "?token=" + encodeURIComponent(TOKEN) : ""}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = async () => { setConn(true); try { render(await api("GET", "/api/state")); } catch (e) {} };
  ws.onmessage = (m) => {
    if (typeof m.data === "string") { const d = JSON.parse(m.data); if (d.t === "state") render(d); }
    else onFrame(m.data);
  };
  ws.onclose = (e) => {
    setConn(false);
    ws = null;
    if (e.code === 4001) { location.href = "/pair?need=1"; return; }   // 被吊销
    setTimeout(connect, 2000);
  };
}
function setConn(on) { $("conn").textContent = on ? "已连接" : "已断开，正在重连…"; $("conn").className = "conn " + (on ? "on" : "off"); }

function onFrame(buf) {
  try {
    const n = new DataView(buf).getUint32(0);
    const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, n)));
    const blob = new Blob([new Uint8Array(buf, 4 + n)], { type: "image/jpeg" });
    if (shotUrl) URL.revokeObjectURL(shotUrl);
    shotUrl = URL.createObjectURL(blob);
    $("shot").src = shotUrl; $("noShot").classList.add("hidden");
    $("shotInfo").textContent = new Date().toLocaleTimeString() + (head.url ? " · " + head.url.slice(0, 60) : "");
  } catch (e) {}
}

// ---------------- 状态 ----------------
function render(st) {
  if (!st || (!st.browser && st.t !== "state")) return;
  S = st;
  const job = st.job, busy = st.busy, p = st.pause;
  $("jobTitle").textContent = job ? job.title : "没有任务在运行";
  $("jobState").textContent = job ? (JOB_STATE[job.state] || job.state) : "";
  const pr = (job && job.progress) || {};
  $("jobLine").textContent = job && pr.budget != null ? `已用 ${pr.used ?? 0} / ${pr.budget} 次 · 节点 ${pr.nodes ?? 0} · 末端叶子 ${pr.leaves ?? 0}` +
    (pr.trial ? "（试跑）" : "") : (st.phase ? `阶段：${st.phase}` : "");
  $("pauseLine").textContent = p ? (PAUSE[p.reason] || p.reason) : "";
  $("bPause").disabled = !busy || !!p;
  $("bResume").disabled = !p || ["budget", "decision", "handoff"].includes(p.reason);
  $("bStop").disabled = !busy;
  const c = st.confirm;
  $("confirmCard").classList.toggle("hidden", !c);
  if (c) $("confirmText").textContent = c.text || "";
  renderAlert(st.alert);
  for (const id of ["bRun", "bTrial"]) $(id).disabled = busy;
}
function renderAlert(a) {
  const box = $("alert");
  if (!a) { box.classList.add("hidden"); lastAlert = ""; return; }
  const key = a.kind + "|" + a.text;
  box.className = "alert " + a.kind;
  $("alertText").textContent = a.kind === "human" ? "需要到电脑前处理：" + a.text : a.text;
  const btns = { confirm: `<button data-a="allow">允许一次</button><button data-a="deny">拒绝</button>`,
    human: `<button data-a="stop">取消任务</button>`, decision: `<button data-a="tree">去任务树处理</button>` }[a.kind] || "";
  $("alertBtns").innerHTML = btns;
  $("alertBtns").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const x = b.dataset.a;
    if (x === "allow") act("/api/jobs/confirm", { allow: true });
    else if (x === "deny") act("/api/jobs/confirm", { allow: false });
    else if (x === "stop") act("/api/jobs/stop");
    else if (x === "tree") showTab("tree");
  }));
  if (key !== lastAlert) { lastAlert = key; ring(); }
}

// ---------------- 提醒：震动 + 提示音（Safari 要求先点一下页面才能出声） ----------------
function unlockAudio() {
  try {
    if (!audio) audio = new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
  } catch (e) {}
}
document.addEventListener("pointerdown", unlockAudio, { once: false, passive: true });
function ring() {
  try { if (navigator.vibrate) navigator.vibrate([200, 100, 200]); } catch (e) {}
  try {
    if (!audio || audio.state !== "running") return;
    const o = audio.createOscillator(), g = audio.createGain();
    o.type = "sine"; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, audio.currentTime);
    g.gain.exponentialRampToValueAtTime(0.3, audio.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.3);
    o.connect(g); g.connect(audio.destination); o.start(); o.stop(audio.currentTime + 0.32);
  } catch (e) {}
}

// ---------------- 标签页 ----------------
function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  if (name === "tree" && !$("treeFrame").src) $("treeFrame").src = $("treeFrame").dataset.src;
  if (name === "books") loadBooks();
  if (name === "runs") loadRuns();
  try { localStorage.setItem("m_tab", name); } catch (e) {}
}
document.querySelectorAll("#nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---------------- 任务书 ----------------
async function loadBooks() {
  try { books = await api("GET", "/api/books"); } catch (e) { $("bookMsg").textContent = e.message; return; }
  const keep = $("bookSel").value;
  $("bookSel").innerHTML = books.map((b) => `<option value="${esc(b.name)}">${esc(b.title)}${b.status !== "ok" ? "（不能运行）" : ""}</option>`).join("");
  if (keep && books.some((b) => b.name === keep)) $("bookSel").value = keep;
  renderBookParams();
}
function renderBookParams() {
  const b = books.find((x) => x.name === $("bookSel").value);
  $("bookParams").innerHTML = b ? b.params.map((p) => `<label>${esc(p.label || p.name)}<input data-bp="${esc(p.name)}" ` +
    `type="${p.type === "int" || p.type === "float" ? "number" : "text"}" value="${esc(p.default ?? "")}"></label>`).join("") : "";
  $("bookMsg").textContent = !b ? "" : b.status === "ok" ? `起始词 ${b.start}；上限 ${b.budget} 次，试跑 ${b.trial_budget} 次` :
    b.status === "needs_rewrite" ? "由旧格式转换而来：点“编辑”用积木补全后才能运行" : `有 ${b.problems} 处问题：点“编辑”查看`;
}
async function runBook(trial) {
  const values = {};
  document.querySelectorAll("[data-bp]").forEach((i) => { if (i.value !== "") values[i.dataset.bp] = i.value; });
  try {
    await api("POST", "/api/books/run", { name: $("bookSel").value, values, budget: +$("bookBudget").value || null, trial });
    $("bookMsg").textContent = trial ? "试跑已开始" : "已开始"; showTab("status");
  } catch (e) { $("bookMsg").textContent = e.message; }
}
function openBuild(url) {
  $("bookList").classList.add("hidden"); $("buildWrap").classList.remove("hidden");
  $("buildFrame").src = url;
}
$("bookSel").addEventListener("change", renderBookParams);
$("bRun").addEventListener("click", () => runBook(false));
$("bTrial").addEventListener("click", () => runBook(true));
$("bEdit").addEventListener("click", () => openBuild(`/build?name=${encodeURIComponent($("bookSel").value)}`));
$("bNewBook").addEventListener("click", () => openBuild("/build"));
$("bReloadBooks").addEventListener("click", loadBooks);
$("bBackBooks").addEventListener("click", () => { $("buildWrap").classList.add("hidden"); $("bookList").classList.remove("hidden"); $("buildFrame").src = "about:blank"; loadBooks(); });

// ---------------- 记录 ----------------
async function loadRuns() {
  try {
    const rows = (await api("GET", "/api/runs")).filter((r) => r.kind === "book").slice(0, 40);
    $("runs").className = "";
    $("runs").innerHTML = rows.map((r) => `<div class="run" data-dir="${esc(r.dir)}" data-report="${esc(r.report || "")}">` +
      `<b>${esc(r.title || r.label)}</b>${r.trial ? ' <span class="tag">试跑</span>' : ""} <span class="muted small">${esc(r.time)}</span><br>` +
      `<span class="small">${esc(r.state === "done" ? "完成" : r.state === "stopped" ? "已停止" : r.state || "")} · 末端叶子 ${r.leaves}</span>` +
      (r.top && Object.keys(r.top).length ? `<br><span class="small muted">${esc(Object.entries(r.top).map(([g, v]) => `${g || "结果"}：${v.join("、")}`).join("；"))}</span>` : "") +
      "</div>").join("") || "还没有任务书运行记录";
    $("runs").querySelectorAll(".run").forEach((el) => el.addEventListener("click", () => {
      if (!el.dataset.report) return;
      $("runList").classList.add("hidden"); $("reportWrap").classList.remove("hidden");
      $("reportFrame").src = `/runs/${encodeURIComponent(el.dataset.dir)}/${el.dataset.report}`;
    }));
  } catch (e) { $("runs").textContent = e.message; }
}
$("bReloadRuns").addEventListener("click", loadRuns);
$("bBackRuns").addEventListener("click", () => { $("reportWrap").classList.add("hidden"); $("runList").classList.remove("hidden"); $("reportFrame").src = "about:blank"; });

// ---------------- 控制 ----------------
$("bPause").addEventListener("click", () => act("/api/jobs/pause"));
$("bResume").addEventListener("click", () => act("/api/jobs/resume"));
$("bStop").addEventListener("click", () => act("/api/jobs/stop"));
$("bAllow").addEventListener("click", () => act("/api/jobs/confirm", { allow: true }));
$("bDeny").addEventListener("click", () => act("/api/jobs/confirm", { allow: false }));

// ---------------- 启动 ----------------
(async function init() {
  try { const me = await api("GET", "/api/remote/me"); $("who").textContent = me.name; }
  catch (e) { $("who").textContent = TOKEN ? "电脑本机" : ""; }
  let tab = "status";
  try { tab = localStorage.getItem("m_tab") || "status"; } catch (e) {}
  showTab(tab);
  connect();
})();
