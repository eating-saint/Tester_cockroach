// Hands 控制面板前端：纯 JS，无外部库。服务端是状态的唯一来源，重连后先拉 /api/state 恢复界面（N7）。
"use strict";
const TOKEN = document.querySelector('meta[name="panel-token"]').content;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS_TEXT = { idle: "未启动", running: "运行中", crash_restarting: "崩溃重启中", need_human: "需要人工", stopped: "已停止" };
const WAIT_TEXT = { load: "页面加载（load）", network_quiet: "网络安静", after_action: "动作后等待", think: "思考时间", human: "等待人工" };
const JOB_STATE = { running: "运行中", need_human: "需要人工", stopping: "停止中", done: "完成", failed: "失败", stopped: "已停止" };

let S = null;              // 最新状态
let INFO = null;           // 设置项表等静态信息
let ws = null;
let frameMeta = null;      // 当前画面帧头
let elements = [];         // 元素框缓存（CSS 坐标）
let hoverEl = null;
let uiMode = "";           // "" | "target" | "manual"
let lastAction = null;     // 最近一次动作（含轨迹）
const ov = { boxes: false, target: true, traj: true, landing: true, over: true, cursor: true };

// ---------------- API ----------------
async function api(method, path, body, raw) {
  const opt = { method, headers: { "X-Panel-Token": TOKEN } };
  if (body !== undefined) {
    if (raw) { opt.body = body; } else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch (e) { j = { ok: r.ok }; }
  if (!r.ok || j.ok === false) {
    const msg = (j && (j.error || j.message)) || r.statusText;
    toast(msg, true);
    const e = new Error(msg); e.status = r.status; e.body = j; throw e;
  }
  return j;
}
const htmlCache = {};
function setHTML(id, html) {   // 内容没变就不碰 DOM：状态推送很频繁
  if (htmlCache[id] === html) return;
  htmlCache[id] = html;
  $(id).innerHTML = html;
}
function toast(msg, bad) {
  const el = $("jobLine");
  el.textContent = msg; el.className = bad ? "bad" : "muted";
}

// ---------------- WebSocket ----------------
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
  ws.binaryType = "arraybuffer";
  ws.onopen = async () => { try { render(await (await fetch("/api/state", { headers: { "X-Panel-Token": TOKEN } })).json()); } catch (e) {} };
  ws.onmessage = (m) => {
    if (typeof m.data === "string") {
      const d = JSON.parse(m.data);
      if (d.t === "state") render(d); else if (d.t === "event") onEvent(d);
    } else onFrame(m.data);
  };
  ws.onclose = () => { ws = null; setTimeout(connect, 1000); };
}
function wsSend(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }

// ---------------- 画面 ----------------
const frame = $("frame"), overlay = $("overlay");
const fctx = frame.getContext("2d"), octx = overlay.getContext("2d");
let drawing = false, pendingFrame = null;
function onFrame(buf) {
  if (drawing) { pendingFrame = buf; return; }
  drawFrame(buf);
}
async function drawFrame(buf) {
  drawing = true;
  try {
    const dv = new DataView(buf);
    const hl = dv.getUint32(0);
    const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hl)));
    const blob = new Blob([new Uint8Array(buf, 4 + hl)], { type: "image/jpeg" });
    const bmp = await createImageBitmap(blob);
    if (frame.width !== bmp.width || frame.height !== bmp.height) {
      frame.width = overlay.width = bmp.width; frame.height = overlay.height = bmp.height;
      layoutCanvas();
    }
    fctx.drawImage(bmp, 0, 0);
    bmp.close && bmp.close();
    const pageChanged = frameMeta && frameMeta.epoch !== head.epoch;
    frameMeta = head;
    wsSend({ t: "shown", seq: head.seq, at: Date.now() / 1000 });
    drawOverlay();
    if (pageChanged && ov.boxes) loadElements();
  } catch (e) { /* 坏帧跳过 */ }
  drawing = false;
  if (pendingFrame) { const b = pendingFrame; pendingFrame = null; drawFrame(b); }
}
function layoutCanvas() {
  const stage = $("stage");
  const k = Math.min(stage.clientWidth / frame.width, stage.clientHeight / frame.height, 4);
  const w = frame.width * k, h = frame.height * k;
  for (const c of [frame, overlay]) { c.style.width = w + "px"; c.style.height = h + "px"; }
}
window.addEventListener("resize", layoutCanvas);
// CSS 像素 <-> 画布像素
function scale() { return frameMeta && frameMeta.dw ? frame.width / frameMeta.dw : (S && S.preview.frame ? 1 : 1); }
function toCss(ev) {
  const r = frame.getBoundingClientRect();
  const px = (ev.clientX - r.left) / r.width * frame.width, py = (ev.clientY - r.top) / r.height * frame.height;
  const k = scale();
  return { x: px / k, y: py / k };
}
function drawOverlay() {
  const k = scale();
  octx.clearRect(0, 0, overlay.width, overlay.height);
  octx.lineWidth = 2;
  if (ov.boxes) {
    octx.font = "11px sans-serif";
    for (const e of elements) {
      if (!e.visible) continue;
      const b = e.box;
      octx.strokeStyle = "rgba(22,163,74,.9)";
      octx.strokeRect(b.x * k, b.y * k, b.width * k, b.height * k);
      octx.fillStyle = "#dc2626"; octx.fillText(e.id, b.x * k + 2, Math.max(10, b.y * k - 2));
    }
  }
  if (hoverEl && uiMode === "target") {
    const b = hoverEl.box;
    octx.fillStyle = "rgba(37,99,235,.25)"; octx.fillRect(b.x * k, b.y * k, b.width * k, b.height * k);
    octx.strokeStyle = "#2563eb"; octx.strokeRect(b.x * k, b.y * k, b.width * k, b.height * k);
  }
  // 演示：上一轮灰色虚线、本轮彩色
  const rounds = (S && S.demo) || [];
  if (ov.traj && rounds.length > 1 && S.job && S.job.kind === "demo") {
    for (const row of rounds[0].rows || []) drawTraj(row.trajectory, k, true);
  }
  if (ov.traj && rounds.length && S.job && S.job.kind === "demo") {
    for (const row of rounds[rounds.length - 1].rows || []) drawTraj(row.trajectory, k, false, true);
  }
  const t = lastAction && lastAction.trajectory;
  if (t) drawTraj(t, k, false);
}
function drawTraj(t, k, ghost, light) {
  if (!t || !t.points) return;
  if (ov.target && t.target && !ghost) {
    octx.strokeStyle = "#1e5ae6"; octx.setLineDash([]);
    octx.strokeRect(t.target.x * k, t.target.y * k, t.target.width * k, t.target.height * k);
  }
  if (ov.traj) {
    const pts = [t.start].concat(t.points.map((p) => [p[0], p[1]]));
    const n = Math.max(pts.length - 1, 1);
    octx.setLineDash(ghost ? [4, 4] : []);
    for (let i = 0; i < pts.length - 1; i++) {
      const q = i / n;
      octx.strokeStyle = ghost ? "rgba(140,140,140,.8)" : `rgba(${40 + 215 * q | 0},${200 - 170 * q | 0},40,${light ? .55 : 1})`;
      octx.beginPath(); octx.moveTo(pts[i][0] * k, pts[i][1] * k); octx.lineTo(pts[i + 1][0] * k, pts[i + 1][1] * k); octx.stroke();
    }
    octx.setLineDash([]);
  }
  if (ghost) return;
  if (ov.over && t.overshoot_point) {
    const [ox, oy] = t.overshoot_point; octx.strokeStyle = "#ff8c00";
    octx.beginPath(); octx.moveTo(ox * k - 5, oy * k - 5); octx.lineTo(ox * k + 5, oy * k + 5);
    octx.moveTo(ox * k - 5, oy * k + 5); octx.lineTo(ox * k + 5, oy * k - 5); octx.stroke();
  }
  if (ov.landing && t.landing) {
    octx.fillStyle = "#dc2626"; octx.beginPath(); octx.arc(t.landing[0] * k, t.landing[1] * k, 4, 0, 7); octx.fill();
  }
  if (ov.cursor && t.landing && !light) {
    const [x, y] = t.landing; octx.fillStyle = "#fff"; octx.strokeStyle = "#000"; octx.lineWidth = 1;
    octx.beginPath(); octx.moveTo(x * k, y * k); octx.lineTo(x * k, y * k + 16); octx.lineTo(x * k + 4, y * k + 12);
    octx.lineTo(x * k + 11, y * k + 12); octx.closePath(); octx.fill(); octx.stroke(); octx.lineWidth = 2;
  }
}
async function loadElements() {
  try { const r = await api("GET", "/api/elements"); elements = r.data.elements || []; } catch (e) { elements = []; }
  drawOverlay();
}

// ---------------- 事件 ----------------
function onEvent(d) {
  if (d.name === "action_done") { if (d.trajectory) lastAction = d; drawOverlay(); if (ov.boxes) loadElements(); }
  if (d.name === "demo_click") { lastAction = { trajectory: d.row.trajectory }; drawOverlay(); }
  if (d.name === "blocked") toast("已拦截外部跳转：" + d.url, true);
  if (d.name === "sample_real") loadGrid();
  if (d.name === "need_human") { setUiMode("manual"); switchTab("run"); }
  if (d.name === "job_end") {
    if (uiMode === "manual" || uiMode === "target") setUiMode("");
    loadRuns(); loadGrid();
  }
}

// ---------------- 渲染状态 ----------------
function render(st) {
  if (!st || st.t !== "state" && !st.browser) return;
  const prevJob = S && S.job && S.job.id;
  S = st;
  const b = st.browser, busy = st.busy, job = st.job;
  if (job && job.id !== prevJob && prevJob !== undefined) { lastAction = null; drawOverlay(); }
  $("lamp").className = "lamp " + b.status;
  $("lampText").textContent = STATUS_TEXT[b.status] || b.status;
  $("crashes").textContent = b.crashes;
  const alive = b.status === "running" || b.status === "need_human";
  $("bStart").disabled = alive || b.need_chrome_path;
  $("bStop").disabled = busy || !alive;
  $("bRestart").disabled = busy || b.need_chrome_path;
  document.querySelectorAll('input[name=mode]').forEach((r) => { r.disabled = busy; r.checked = (r.value === "headless") === b.headless; });
  $("bFront").disabled = !alive;
  $("chromeBar").classList.toggle("hidden", !b.need_chrome_path);
  $("running").textContent = busy && job ? `正在运行：${job.title}` : "";
  // 地址栏
  const addr = $("addr");
  addr.readOnly = busy && !(job && (job.kind === "manual" || job.state === "need_human"));
  if (document.activeElement !== addr) addr.value = st.page.url || "";
  $("title").textContent = st.page.title || "";
  $("bBack").disabled = $("bReload").disabled = addr.readOnly;
  // 人工
  const need = job && job.state === "need_human";
  $("humanBar").classList.toggle("hidden", !need);
  if (need) $("humanText").textContent = (job.need_human && job.need_human.url) || "";
  if (need && uiMode !== "manual") setUiMode("manual");
  // 预览
  const pv = st.preview;
  $("noPreview").classList.toggle("hidden", pv.on || !alive || b.headless);
  if (!alive) { $("noPreview").classList.remove("hidden"); $("noPreviewText").textContent = "浏览器未运行"; }
  else $("noPreviewText").textContent = "浏览器窗口在屏幕上（有界面模式不推流）";
  $("fps").textContent = pv.on ? `${pv.fps} 帧/秒` + (pv.latency_ms_p50 != null ? ` · 延迟 ${pv.latency_ms_p50} ms` : "") : "推流关";
  // 启动按钮
  for (const id of ["bDemo", "bDemoReplay", "bCase", "bTargetMode", "bManual"]) $(id).disabled = busy || b.need_chrome_path;
  if (uiMode === "target") $("bTargetMode").disabled = false;
  if (job && job.kind === "manual" && busy) { $("bManual").disabled = false; $("bManual").textContent = "退出手动模式"; }
  else $("bManual").textContent = "进入手动模式";
  $("bStopJob").disabled = !busy;
  if (job) {
    const p = job.progress || {};
    $("jobLine").className = "muted";
    $("jobLine").textContent = `${job.title}：${JOB_STATE[job.state] || job.state}` +
      (p.max_steps ? `（第 ${p.step || 0} / ${p.max_steps} 步）` : "") + (p.seed != null ? ` 种子 ${p.seed}` : "");
  }
  renderSampleReal(st); renderDemo(st); renderCaseResult(st); renderTarget(st); renderTree(st); renderNet(st); renderSettingsState(st);
  $("mode").textContent = uiMode === "target" ? "点选模式" : uiMode === "manual" ? "手动模式" : "";
}
function renderSampleReal(st) {
  const j = st.job, on = !!(j && j.kind === "sample_real" && st.busy);
  $("bSrStart").disabled = st.busy || st.browser.need_chrome_path;
  $("bSrStop").disabled = !on;
  $("bSrBack").disabled = !on;
  if (!j || j.kind !== "sample_real") { $("srMsg").classList.add("hidden"); return; }
  const p = j.progress || {};
  $("srMsg").classList.remove("hidden");
  $("srMsg").innerHTML = (on ? `<b>${esc(p.msg || "")}</b><br>` : `<b>采集已结束：${esc((j.result && j.result.reason) || "")}</b><br>`) +
    `已保存 ${p.saved || 0} 条，作废 ${p.rejected || 0} 条` +
    (p.question ? `　当前目标：${esc(p.question.name || "（无名称）")}` : "");
}
function renderDemo(st) {
  const rounds = st.demo || [];
  const cur = rounds[rounds.length - 1];
  if (!cur) return;
  const rows = cur.rows || [];
  const hits = rows.filter((r) => r.hit).length;
  $("demoSummary").textContent = `种子 ${cur.seed}　命中 ${hits}/${rows.length}` + (rounds.length > 1 ? `　（上一轮种子 ${rounds[0].seed}，灰色虚线）` : "");
  setHTML("demoTable", "<tr><th>#</th><th>按钮</th><th>命中</th><th>计划ms</th><th>实际ms</th><th>来源/样本</th><th>过冲</th></tr>" +
    rows.map((r) => `<tr><td>${r.i}</td><td>${esc(r.name)}<span class="muted">${r.role === "目标" ? "" : "·" + esc(r.role)}</span></td>` +
      `<td class="${r.hit ? "ok" : "bad"}">${r.hit ? "是" : "否"}</td><td>${r.planned_ms ?? ""}</td><td>${r.actual_ms ?? ""}</td>` +
      `<td>${r.source === "library" ? esc(r.sample_id) : r.fallback ? "退回模板" : "模板"}</td><td>${r.overshoot ? "有" : ""}</td></tr>`).join(""));
}
function renderCaseResult(st) {
  const j = st.job;
  if (!j || j.kind !== "case" || !j.result) { if (j && j.kind === "case") setHTML("caseResult", ""); return; }
  const r = j.result;
  setHTML("caseResult", `<div class="box"><b class="${r.passed ? "ok" : "bad"}">${r.passed ? "通过" : "失败"}</b> ${esc(r.reason === "user_stopped" ? "用户停止" : r.reason || "")}` +
    (r.report ? `　<a href="#" data-report="${esc(r.report)}">打开报告</a>` : "") +
    ((r.human_handoffs || []).length ? `<br>人工介入 ${r.human_handoffs.length} 次` : "") + "</div>");
}
function renderTarget(st) {
  const j = st.job;
  if (!j || j.kind !== "target" || !j.result) return;
  const r = j.result;
  setHTML("targetResult", `<div class="box">${r.element ? `点了 <b>${esc(r.element.role)}</b> “${esc(r.element.name)}”：` : ""}` +
    `<span class="${r.passed ? "ok" : "bad"}">${esc(r.reason)}</span>` +
    (r.trajectory ? `　轨迹 ${r.trajectory.n_points} 点 ${r.trajectory.duration_ms} ms` : "") + "</div>");
  if (r.trajectory) lastAction = { trajectory: r.trajectory };
}
function renderTree(st) {
  const t = st.tree;
  if (t) {
    const byParent = {};
    for (const n of t.nodes) (byParent[n.parent || ""] = byParent[n.parent || ""] || []).push(n);
    const walk = (pid) => (byParent[pid] || []).map((n) =>
      `<div class="tree-node ${n.status} ${n.id === t.active_id && !t.finished ? "active" : ""}" style="margin-left:${n.depth * 16}px">` +
      `<b>${esc(n.goal)}</b> <span class="muted">[${n.status}] ${n.steps_used}/${t.max_steps} 步</span>` +
      (n.summary ? `<div class="small">摘要：${esc(n.summary)}</div>` : "") + "</div>" + walk(n.id)).join("");
    setHTML("tree", walk(""));
  }
  const steps = st.steps || [];
  setHTML("steps", "<tr><th>#</th><th>工具</th><th>参数</th><th>生效</th><th>变化</th><th>CHECK</th><th>ANALYZE</th><th>EXECUTE</th></tr>" +
    steps.map((s) => `<tr><td>${s.index}</td><td>${esc(s.tool)}</td><td><code>${esc(JSON.stringify(s.arguments || {})).slice(0, 80)}</code></td>` +
      `<td class="${s.ok ? (s.effective ? "ok" : "") : "bad"}">${s.ok ? (s.effective ? "是" : "否") : "错误"}</td><td>${esc((s.changes || []).join(","))}${s.error ? " " + esc(s.error) : ""}</td>` +
      `<td>${s.durations ? s.durations.check : ""}</td><td>${s.durations ? s.durations.decide : ""}</td><td>${s.durations ? s.durations.execute : ""}</td></tr>`).join(""));
}
function renderNet(st) {
  const w = st.wait, m = st.monitor;
  let txt = w ? `等待：<b>${esc(WAIT_TEXT[w.what] || w.what)}</b>　已等 ${(w.elapsed_ms / 1000).toFixed(1)} 秒` +
    (w.timeout_ms ? ` / 上限 ${(w.timeout_ms / 1000).toFixed(0)} 秒` : "") : "等待：无";
  if (!w && st.steps && st.steps.length) {
    const last = st.steps[st.steps.length - 1];
    if (last && last.error && /timeout/.test(last.error)) txt += "　<b class='bad'>超时</b>";
  }
  if (st.lastWait === "timeout") txt += "　<b class='bad'>超时</b>";
  txt += `　阶段：${esc(st.phase || "-")}　加载：${esc(m ? m.lifecycle : "-")}`;
  setHTML("waitBox", txt);
  const li = (arr, f) => (arr || []).map((x) => `<li data-copy="${esc(x.url || x.text || "")}">${f(x)}</li>`).join("") || "<li class='muted'>无</li>";
  setHTML("pending", li(m && m.pending, (x) => esc(x.url)));
  setHTML("failed", li(m && m.failed, (x) => `${esc(x.status || x.error_text || "")} ${esc(x.url)}`));
  setHTML("console", li(m && m.console_errors, (x) => esc(x.text)));
}
document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copy]");
  if (c && navigator.clipboard) { navigator.clipboard.writeText(c.dataset.copy).catch(() => {}); toast("已复制"); }
  const rp = e.target.closest("[data-report]");
  if (rp) { e.preventDefault(); openReport(rp.dataset.report); }
});

// ---------------- 设置 ----------------
let settingsDirty = {};
function buildSettings() {
  const groups = {};
  for (const s of INFO.specs) (groups[s.group] = groups[s.group] || []).push(s);
  const f = $("settingsForm");
  f.innerHTML = "";
  for (const [g, specs] of Object.entries(groups)) {
    const fs = document.createElement("fieldset");
    fs.innerHTML = `<legend>${esc(g)}</legend>`;
    for (const s of specs) {
      const row = document.createElement("div"); row.className = "row";
      let input;
      if (s.kind === "bool") input = `<input type="checkbox" data-path="${s.path}">`;
      else if (s.kind === "enum") input = `<select data-path="${s.path}">` + s.choices.map((c) =>
        `<option value="${c}" ${(s.disabled_choices || []).includes(c) ? "disabled" : ""}>${esc(CHOICE_TEXT[c] || c)}${(s.disabled_choices || []).includes(c) ? "（未接入）" : ""}</option>`).join("") + "</select>";
      else input = `<input type="text" data-path="${s.path}" placeholder="${s.kind === "seed" ? "留空=随机" : ""}">`;
      const range = s.min != null && s.kind !== "seed" ? `<span class="muted small">${s.min}–${s.max}</span>` : "";
      row.innerHTML = `<label>${esc(s.label)}</label>${input}${range}<span class="err" data-err="${s.path}"></span>`;
      fs.appendChild(row);
    }
    f.appendChild(fs);
  }
  f.addEventListener("input", (e) => { const p = e.target.dataset.path; if (p) settingsDirty[p] = true; });
  $("about").textContent = INFO.about;
}
const CHOICE_TEXT = { rule: "规则决策", vlm: "视觉大模型", template: "参数模板", library: "真实轨迹库", mouse: "鼠标", touchpad: "触控板", mixed: "两者混合" };
function renderSettingsState(st) {
  if (!INFO) return;
  const eff = { ...st.settings.current, ...st.settings.pending };
  document.querySelectorAll("#settingsForm [data-path]").forEach((el) => {
    const p = el.dataset.path;
    el.classList.toggle("pending", p in st.settings.pending);
    if (settingsDirty[p] || document.activeElement === el) return;
    const v = eff[p];
    if (el.type === "checkbox") el.checked = !!v; else el.value = v == null ? "" : v;
  });
  $("settingsMsg").textContent = st.settings_pending ? "有修改将在下一个动作开始时生效" : "";
}
function collectSettings() {
  const out = {};
  document.querySelectorAll("#settingsForm [data-path]").forEach((el) => {
    const p = el.dataset.path, spec = INFO.specs.find((s) => s.path === p);
    let v = el.type === "checkbox" ? el.checked : el.value.trim();
    if (spec.kind === "seed" && v === "") v = null;
    out[p] = v;
  });
  return out;
}
async function saveSettings() {
  document.querySelectorAll("[data-err]").forEach((e) => (e.textContent = ""));
  try { await api("PUT", "/api/settings", collectSettings()); settingsDirty = {}; toast("设置已应用"); return true; }
  catch (e) {
    const errs = (e.body && e.body.errors) || {};
    for (const [p, m] of Object.entries(errs)) { const el = document.querySelector(`[data-err="${p}"]`); if (el) el.textContent = m; }
    $("settingsMsg").textContent = "有取值超出范围，未保存";
    return false;
  }
}

// ---------------- 历史 / 用户目录 ----------------
async function loadRuns() {
  try {
    const r = await api("GET", "/api/runs");
    $("runs").innerHTML = "<tr><th>时间</th><th>标签</th><th>结果</th><th></th></tr>" + r.data.map((x) =>
      `<tr><td>${esc(x.time)}</td><td>${esc(x.label)}</td><td class="${x.passed === x.total ? "ok" : "bad"}">${x.passed}/${x.total}</td>` +
      `<td>${x.has_html ? `<a href="#" data-report="${esc(x.dir)}/report.html">打开</a>` : ""}</td></tr>`).join("");
  } catch (e) {}
}
function openReport(rel) {
  switchTab("history");
  const f = $("report");
  f.src = "/runs/" + rel.split("/").map(encodeURIComponent).join("/");
}
$("report").addEventListener("load", () => {
  // 报告里的链接：站内（截图、轨迹）允许，外部一律拦下
  try {
    const doc = $("report").contentDocument;
    doc.addEventListener("click", (e) => {
      const a = e.target.closest("a"); if (!a) return;
      const u = new URL(a.getAttribute("href"), doc.location.href);
      if (u.origin !== location.origin) { e.preventDefault(); toast("已拦截外部链接：" + u.href, true); }
    }, true);
  } catch (e) {}
});
async function loadProfile() {
  try {
    const r = await api("GET", "/api/profile"); const d = r.data;
    $("profileInfo").innerHTML = `位置：<code>${esc(d.path)}</code><br>占用：${(d.bytes / 1048576).toFixed(1)} MB` +
      (d.browser_running ? "<br><span class='muted'>浏览器运行中，不能清空</span>" : "");
    $("bClear").disabled = d.browser_running;
  } catch (e) {}
}

// ---------------- 交互 ----------------
function switchTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + name));
  if (name === "history") loadRuns();
  if (name === "profile") loadProfile();
  if (name === "collect") loadGrid();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
async function loadGrid() {
  try {
    const r = await api("GET", "/api/samples/grid");
    const names = ["右", "右下", "下", "左下", "左", "左上", "上", "右上"];
    $("grid").innerHTML = Object.entries(r.data).map(([dev, g]) => `<b>${dev === "mouse" ? "鼠标" : "触控板"}</b> ` +
      names.map((n, i) => `${n}:${g[i].local}/${g[i].real}`).join("　")).join("<br>");
  } catch (e) {}
}
function setUiMode(m) {
  uiMode = m;
  $("stage").classList.toggle("target", m === "target");
  $("stage").classList.toggle("manual", m === "manual");
  $("bTargetMode").textContent = m === "target" ? "退出点选模式" : "进入点选模式";
  $("mode").textContent = m === "target" ? "点选模式" : m === "manual" ? "手动模式" : "";
  if (m === "target") loadElements();
  if (m === "manual") $("ime").focus();
  hoverEl = null; drawOverlay();
}
const click = (id, fn) => $(id).addEventListener("click", fn);
click("bSrStart", () => {
  const device = $("srDevice").value;
  if (!device) { toast("请先选择设备（鼠标 / 触控板）", true); return; }
  api("POST", "/api/jobs", { kind: "sample_real", params: { device } }).catch(() => {});
});
click("bSrStop", () => api("POST", "/api/jobs/stop").then(loadGrid).catch(() => {}));
click("bSrBack", () => api("POST", "/api/nav/back").catch(() => {}));
click("bStart", () => api("POST", "/api/browser/start").catch(() => {}));
click("bStop", () => api("POST", "/api/browser/stop").catch(() => {}));
click("bRestart", () => api("POST", "/api/browser/restart").catch(() => {}));
document.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", () =>
  api("POST", "/api/browser/mode", { headless: r.value === "headless" }).catch(() => {})));
const front = () => api("POST", "/api/browser/front").catch(() => {});
click("bFront", front); click("bFront2", front);
click("bPreviewOn", () => api("POST", "/api/preview", { on: true }).catch(() => {}));
click("bPreviewToggle", () => api("POST", "/api/preview", { on: !(S && S.preview.want_on) }).catch(() => {}));
click("bChromePath", () => api("POST", "/api/chrome_path", { path: $("chromePath").value }).catch(() => {}));
$("addr").addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("addr").readOnly) api("POST", "/api/nav", { url: $("addr").value }).catch(() => {}); });
click("bBack", () => api("POST", "/api/nav/back").catch(() => {}));
click("bReload", () => api("POST", "/api/nav/reload").catch(() => {}));
click("bDemo", async () => { if (Object.keys(settingsDirty).length && !(await saveSettings())) return;
  lastAction = null; api("POST", "/api/jobs", { kind: "demo", params: { seed: $("demoSeed").value.trim() || null } }).catch(() => {}); });
click("bDemoReplay", () => { const r = S && S.demo && S.demo[S.demo.length - 1];
  if (!r) return toast("还没有上一轮", true); lastAction = null;
  api("POST", "/api/jobs", { kind: "demo", params: { seed: r.seed } }).catch(() => {}); });
click("bCase", () => api("POST", "/api/jobs", { kind: "case", params: { case: $("caseSel").value } }).then(() => switchTab("task")).catch(() => {}));
click("bTargetMode", () => setUiMode(uiMode === "target" ? "" : "target"));
click("bManual", () => {
  if (S && S.job && S.job.kind === "manual" && S.busy) api("POST", "/api/jobs/stop").catch(() => {});
  else api("POST", "/api/jobs", { kind: "manual", params: {} }).then(() => setUiMode("manual")).catch(() => {});
});
click("bStopJob", () => api("POST", "/api/jobs/stop").catch(() => {}));
click("bContinue", () => api("POST", "/api/jobs/continue").then(() => setUiMode("")).catch(() => {}));
click("bSaveSettings", saveSettings);
click("bSaveDefault", async () => { if (await saveSettings()) api("POST", "/api/settings/save_default").then(() => toast("已保存为默认")).catch(() => {}); });
click("bRestore", () => { settingsDirty = {}; api("POST", "/api/settings/restore").then(() => toast("已恢复默认")).catch(() => {}); });
click("bRuns", loadRuns);
click("bProfile", loadProfile);
click("bClear", () => $("clearConfirm").classList.remove("hidden"));
click("bClearCancel", () => $("clearConfirm").classList.add("hidden"));
click("bClearGo", async () => {
  if ($("clearText").value.trim() !== "清空") return toast("请输入“清空”两个字", true);
  try { await api("POST", "/api/profile/clear", { confirm: "CLEAR" }); $("profileMsg").textContent = "已清空"; } catch (e) { $("profileMsg").textContent = e.message; }
  $("clearConfirm").classList.add("hidden"); $("clearText").value = ""; loadProfile();
});
document.querySelectorAll("[data-ov]").forEach((c) => c.addEventListener("change", () => {
  ov[c.dataset.ov] = c.checked; if (c.dataset.ov === "boxes" && c.checked) loadElements(); drawOverlay();
}));

// 预览上的鼠标：点选模式高亮 / 点击；手动模式转发
let moveQueued = null;
function mods(e) { return { alt: e.altKey, ctrl: e.ctrlKey, meta: e.metaKey, shift: e.shiftKey }; }
frame.addEventListener("mousemove", (e) => {
  const p = toCss(e);
  if (uiMode === "target") {
    let best = null;
    for (const el of elements) {
      const b = el.box;
      if (el.visible && p.x >= b.x && p.x <= b.x + b.width && p.y >= b.y && p.y <= b.y + b.height &&
        (!best || b.width * b.height < best.box.width * best.box.height)) best = el;
    }
    if (best !== hoverEl) { hoverEl = best; drawOverlay(); }
  } else if (uiMode === "manual") {
    if (!moveQueued) requestAnimationFrame(() => { wsSend(moveQueued); moveQueued = null; });
    moveQueued = { t: "mouse", kind: "move", x: p.x, y: p.y, mods: mods(e) };
  }
});
frame.addEventListener("mousedown", (e) => {
  if (uiMode !== "manual") return;
  e.preventDefault(); const p = toCss(e);
  wsSend({ t: "mouse", kind: "down", x: p.x, y: p.y, button: e.button, detail: e.detail, mods: mods(e) });
  setTimeout(() => $("ime").focus(), 0);
});
frame.addEventListener("mouseup", (e) => {
  if (uiMode !== "manual") return;
  const p = toCss(e); wsSend({ t: "mouse", kind: "up", x: p.x, y: p.y, button: e.button, mods: mods(e) });
});
frame.addEventListener("click", (e) => {
  if (uiMode !== "target") return;
  const p = toCss(e);
  api("POST", "/api/jobs", { kind: "target", params: { x: p.x, y: p.y } }).catch(() => {});
  setUiMode("");
});
frame.addEventListener("contextmenu", (e) => { if (uiMode === "manual") e.preventDefault(); });
frame.addEventListener("wheel", (e) => {
  if (uiMode !== "manual") return;
  e.preventDefault(); const p = toCss(e);
  const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;
  wsSend({ t: "mouse", kind: "wheel", x: p.x, y: p.y, dx: e.deltaX * k, dy: e.deltaY * k, mods: mods(e) });
}, { passive: false });
const ime = $("ime");
let composing = false;
ime.addEventListener("compositionstart", () => { composing = true; });
ime.addEventListener("compositionend", (e) => { composing = false; if (e.data) wsSend({ t: "text", text: e.data }); ime.value = ""; });
ime.addEventListener("keydown", (e) => {
  if (uiMode !== "manual" || composing || e.isComposing || e.key === "Process") return;
  e.preventDefault();
  wsSend({ t: "key", kind: "down", key: e.key, code: e.code, mods: mods(e) });
});
ime.addEventListener("keyup", (e) => {
  if (uiMode !== "manual" || composing || e.isComposing || e.key === "Process") return;
  wsSend({ t: "key", kind: "up", key: e.key, code: e.code, mods: mods(e) });
});
ime.addEventListener("input", () => { if (!composing) ime.value = ""; });
$("stage").addEventListener("click", () => { if (uiMode === "manual") ime.focus(); });

// 面板窗口内的外部跳转兜底拦截（N2）
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.dataset.report !== undefined) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin) { e.preventDefault(); toast("已拦截外部链接：" + u.href, true); }
}, true);
window.open = () => { toast("已拦截新窗口", true); return null; };
window.__blocked = (u) => toast("已拦截：" + u, true);   // 窗口原生拦截（app.py）回调
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => { e.preventDefault(); toast("已拦截拖放", true); });

// ---------------- 启动 ----------------
(async function init() {
  try {
    INFO = (await api("GET", "/api/info")).data;
    buildSettings();
    const c = await api("GET", "/api/cases");
    $("caseSel").innerHTML = c.data.map((x) => `<option>${esc(x)}</option>`).join("");
    const pick = c.data.indexOf("t13_reddit_yes.yaml"); if (pick >= 0) $("caseSel").selectedIndex = pick;
  } catch (e) {}
  connect();
  layoutCanvas();
})();
