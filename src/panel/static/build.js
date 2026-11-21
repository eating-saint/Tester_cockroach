// 搭建任务书：从 12 个积木里选、按参数规格填表、校验、预览、试跑、保存。电脑面板与平板共用。
// 任务书对象 B 与文件内容互转：表单 → toYaml()（JSON 风格的 YAML，程序按原样读回）；文件 → 服务器解析出的 book。
"use strict";
const TOKEN = (document.querySelector('meta[name="panel-token"]') || {}).content || "";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const STATUS = { ok: "可运行", draft: "草稿（有问题）", needs_rewrite: "需要用积木改写", new: "新建" };
const TYPES = ["text", "int", "float", "bool"];
if (window.self !== window.top) document.body.classList.add("embed");

let REG = { blocks: [], builtin_lists: [] }, BY = {};
let B = blank();
let cur = { name: null, rev: null, header: "" };     // 打开的文件
let selPath = null;                                  // 选中的积木：[3] 或 [3, 0]（flow[3].open.flow[0]）
let dirty = false, problems = [], badParam = null, textMode = false;

// ---------------- 接口 ----------------
async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (TOKEN) opt.headers["X-Panel-Token"] = TOKEN;
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (r.status === 401) { location.href = "/pair"; throw new Error("需要先配对"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) { const e = new Error(j.error || r.statusText); e.status = r.status; e.body = j; throw e; }
  return j.data;
}
function msg(t, bad) { $("msg").textContent = t || ""; $("msg").className = bad ? "bad small" : "muted small"; }

// ---------------- 任务书对象 ----------------
function blank() {
  return { format: "taskbook/1", name: "", title: "", note: "", params: [], groups: [["", []]], start: "",
    limits: { budget: 120, keep_tabs: 30, trial_budget: 5 }, flow: [], output: { type: "leaves" } };
}
function fromBook(b) {         // 服务器解析出的 book（groups 为 [{键: [值]}]）
  return { format: "taskbook/1", name: b.name || "", title: b.title || "", note: b.note || "",
    params: (b.params || []).map((p) => ({ ...p })), groups: (b.groups || []).map((g) => { const [k, v] = Object.entries(g)[0]; return [k, [...v]]; }),
    start: b.start || "", limits: { budget: 120, keep_tabs: 30, trial_budget: 5, ...(b.limits || {}) },
    flow: JSON.parse(JSON.stringify(b.flow || [])), output: { type: "leaves", ...(b.output || {}) } };
}
const J = (v) => JSON.stringify(v);
function toYaml() {
  const L = [];
  if (cur.header) L.push(cur.header.trimEnd());
  L.push("format: taskbook/1", `name: ${J(B.name)}`, `title: ${J(B.title)}`);
  if (B.note) L.push(`note: ${J(B.note)}`);
  if (B.params.length) { L.push("params:"); B.params.forEach((p) => L.push(`  - ${J(clean(p))}`)); }
  L.push("groups:");
  B.groups.filter(([k]) => k.trim()).forEach(([k, v]) => L.push(`  - ${J(k.trim())}: ${J(v)}`));
  L.push(`start: ${J(B.start)}`, `limits: ${J(B.limits)}`);
  L.push("flow:");
  if (!B.flow.length) L[L.length - 1] = "flow: []";
  B.flow.forEach((it) => { const id = bid(it); L.push(`  - ${id}: ${J(cleanParams(it[id]))}`); });
  L.push(`output: ${J(B.output)}`);
  return L.join("\n") + "\n";
}
function clean(o) {   // 开放参数：空的说明去掉，默认值即使是空字符串也保留
  const out = {};
  for (const [k, v] of Object.entries(o)) if (k === "default" ? v !== undefined && v !== null : v !== "" && v !== null && v !== undefined) out[k] = v;
  return out;
}
function cleanParams(p) {
  const out = {};
  for (const [k, v] of Object.entries(p || {})) {
    if (v === "" || v === null || v === undefined || (Array.isArray(v) && !v.length && k !== "flow")) continue;
    out[k] = k === "flow" && Array.isArray(v) ? v.map((it) => { const id = bid(it); return { [id]: cleanParams(it[id]) }; }) : v;
  }
  return out;
}
const bid = (it) => Object.keys(it)[0];
const prm = (it) => (it[bid(it)] = it[bid(it)] || {});
function headerOf(text) {
  const out = [];
  for (const line of (text || "").split(/\r?\n/)) { if (/^\s*#/.test(line) || (!line.trim() && out.length)) out.push(line); else break; }
  return out.join("\n").trim();
}

// ---------------- 路径 ----------------
function container(path) {       // path 去掉最后一位后所在的积木列表
  let list = B.flow;
  for (const i of path.slice(0, -1)) { const p = prm(list[i]); p.flow = p.flow || []; list = p.flow; }
  return list;
}
const itemAt = (path) => path && container(path)[path[path.length - 1]];
const pathKey = (path) => path.map((i, n) => (n ? `.open.flow[${i}]` : `flow[${i}]`)).join("");
function walk(list, prefix, fn) {                    // 执行顺序（前序）
  list.forEach((it, i) => { const p = [...prefix, i]; fn(it, p); if (bid(it) === "open") walk(prm(it).flow || [], p, fn); });
}
function listsBefore(path) {
  const key = pathKey(path), out = [...REG.builtin_lists];
  let stop = false;
  walk(B.flow, [], (it, p) => {
    if (stop) return;
    if (pathKey(p) === key) { stop = true; return; }
    const spec = BY[bid(it)];
    if (!spec) return;
    for (const ps of spec.params) if (ps.type === "out" && prm(it)[ps.name]) out.push(prm(it)[ps.name]);
  });
  return [...new Set(out)];
}

// ---------------- 画表单 ----------------
function renderAll() {
  $("fName").value = B.name; $("fTitle").value = B.title; $("fNote").value = B.note;
  renderParams(); renderGroups(); renderLimits(); renderOutput(); renderFlow(); renderForm(); renderStatus();
}
function renderStatus(status) {
  const s = status || (cur.rev ? "" : "new");
  if (status !== undefined || !cur.rev) { $("status").textContent = STATUS[s] || s; $("status").className = "tag " + s; }
  $("dirty").textContent = dirty ? "有未保存的改动" : (cur.name ? `已保存：tasks/${cur.name}.yaml` : "");
}
function markDirty() { dirty = true; renderStatus(); scheduleValidate(); }

function renderParams() {
  $("params").innerHTML = B.params.map((p, i) => `<div class="prow" data-i="${i}">
    <input class="n" data-k="name" value="${esc(p.name)}" placeholder="参数名">
    <select data-k="type">${TYPES.map((t) => `<option${(p.type || "text") === t ? " selected" : ""}>${t}</option>`).join("")}</select>
    <input class="d" data-k="default" value="${esc(p.default ?? "")}" placeholder="默认值">
    <input class="l" data-k="label" value="${esc(p.label || "")}" placeholder="说明">
    <button class="mini danger" data-del>✕</button></div>`).join("") || `<p class="muted small">（没有）</p>`;
  $("params").querySelectorAll(".prow").forEach((row) => {
    const p = B.params[+row.dataset.i];
    row.querySelectorAll("[data-k]").forEach((el) => el.addEventListener("change", () => {
      let v = el.value;
      if (el.dataset.k === "default" && (p.type === "int" || p.type === "float") && v !== "") v = Number(v);
      if (el.dataset.k === "default" && p.type === "bool" && v !== "") v = ["true", "1", "是", "yes"].includes(String(v).toLowerCase());
      p[el.dataset.k] = v; markDirty(); renderFlow();
    }));
    row.querySelector("[data-del]").addEventListener("click", () => { B.params.splice(+row.dataset.i, 1); renderParams(); markDirty(); });
  });
}
function renderGroups() {
  $("groups").innerHTML = B.groups.map(([k, v], i) => `<div class="grp" data-i="${i}">
    <input class="k" value="${esc(k)}" placeholder="键">
    <input class="v" value="${esc(v.join("、"))}" placeholder="值，用逗号或顿号分隔">
    <button class="mini" data-up>↑</button><button class="mini" data-down>↓</button><button class="mini danger" data-del>✕</button></div>`).join("");
  $("groups").querySelectorAll(".grp").forEach((row) => {
    const i = +row.dataset.i;
    row.querySelector(".k").addEventListener("change", (e) => { B.groups[i][0] = e.target.value.trim(); renderStart(); markDirty(); });
    row.querySelector(".v").addEventListener("change", (e) => { B.groups[i][1] = splitList(e.target.value); renderStart(); markDirty(); });
    row.querySelector("[data-up]").addEventListener("click", () => { move(B.groups, i, -1); renderGroups(); markDirty(); });
    row.querySelector("[data-down]").addEventListener("click", () => { move(B.groups, i, 1); renderGroups(); markDirty(); });
    row.querySelector("[data-del]").addEventListener("click", () => { B.groups.splice(i, 1); renderGroups(); markDirty(); });
  });
  renderStart();
}
function renderStart() {
  const words = [...new Set(B.groups.flatMap(([k, v]) => [k, ...v]).filter(Boolean))];
  if (B.start && !words.includes(B.start)) words.unshift(B.start);
  $("fStart").innerHTML = `<option value="">（选起始词）</option>` + words.map((w) => `<option${w === B.start ? " selected" : ""}>${esc(w)}</option>`).join("");
}
function renderLimits() { $("lBudget").value = B.limits.budget ?? ""; $("lTabs").value = B.limits.keep_tabs ?? ""; $("lTrial").value = B.limits.trial_budget ?? ""; }
function renderOutput() {
  const o = B.output;
  $("oType").value = o.type || "leaves";
  $("rankedBox").classList.toggle("hidden", o.type !== "ranked");
  $("oSort").value = (o.sort || []).join(", "); $("oN").value = o.n ?? 3;
  $("oGroup").checked = o.group_by === "key"; $("oUnknown").value = o.unknown || "last";
}
function splitList(s) { return String(s || "").split(/[,，、\n]/).map((x) => x.trim()).filter(Boolean); }
function move(arr, i, d) { const j = i + d; if (j < 0 || j >= arr.length) return false; [arr[i], arr[j]] = [arr[j], arr[i]]; return true; }

// ---------------- 流程列表 ----------------
function summary(it) {
  const id = bid(it), p = prm(it), spec = BY[id];
  if (!spec) return "";
  const keys = { search: ["query"], scroll_all: ["mode", "text"], pick_list: ["region", "max"], open: ["from", "n", "count"],
    read_fields: ["names"], ratio: ["in", "words", "at_least"], rank: ["in", "k", "top_k"], filter: ["in", "only_known", "drop_current"],
    tree: ["op", "use"], stop: ["when", "action"], page: ["to", "next"], compare: ["in", "field", "op", "value"] }[id] || [];
  return keys.filter((k) => p[k] !== undefined && p[k] !== "").map((k) => `${k}=${Array.isArray(p[k]) ? p[k].join("、") : p[k]}`).join("  ");
}
function errSteps() { return new Set(problems.filter((p) => /^flow\[/.test(p.step)).map((p) => p.step)); }
function blockHtml(it, path, errs) {
  const id = bid(it), spec = BY[id] || { label: id, actor: "", params: [] }, p = prm(it), key = pathKey(path);
  const outs = spec.params.filter((x) => x.type === "out" && p[x.name]).map((x) => p[x.name]);
  const sub = id === "open" ? `<div class="sub">${(p.flow || []).map((s, i) => blockHtml(s, [...path, i], errs)).join("") ||
    `<div class="empty">（打开后做什么：选中这一块，再用下面的“加到末尾”加积木）</div>`}</div>` : "";
  const sel = selPath && pathKey(selPath) === key;
  return `<div class="blk${sel ? " sel" : ""}${errs.has(key) ? " err" : ""}" data-path="${path.join(",")}">
    <div class="head"><span class="no">${path.map((x) => x + 1).join(".")}</span>
      <span class="lab">${esc(spec.label)}</span>${spec.actor === "4b" ? `<span class="actor4b">4b 选编号</span>` : ""}
      <span class="sum">${esc(summary(it))}</span>
      ${outs.length ? `<span class="out">→ ${esc(outs.join("、"))}</span>` : ""}
      <span class="ops"><button class="mini" data-act="up">↑</button><button class="mini" data-act="down">↓</button>
      <button class="mini danger" data-act="del">✕</button></span></div>${sub}</div>`;
}
function renderFlow() {
  const errs = errSteps();
  $("flow").innerHTML = B.flow.map((it, i) => blockHtml(it, [i], errs)).join("") || `<p class="muted small">（还没有积木：从下面选一个加进来）</p>`;
  $("flow").querySelectorAll(".blk").forEach((el) => {
    const path = el.dataset.path.split(",").map(Number);
    el.querySelector(".head").addEventListener("click", (e) => {
      e.stopPropagation();
      const act = e.target.dataset && e.target.dataset.act;
      if (act === "up" || act === "down") {
        const list = container(path), i = path[path.length - 1];
        if (move(list, i, act === "up" ? -1 : 1)) { selPath = [...path.slice(0, -1), i + (act === "up" ? -1 : 1)]; markDirty(); }
      } else if (act === "del") {
        container(path).splice(path[path.length - 1], 1); selPath = null; markDirty();
      } else { selPath = selPath && pathKey(selPath) === pathKey(path) ? null : path; badParam = null; }
      renderFlow(); renderForm();
    });
  });
  const s = itemAt(selPath);
  $("addWhere").textContent = s && bid(s) === "open" ? `加到「打开第 N 个」${selPath.map((x) => x + 1).join(".")} 的子流程里` : "加到节点流程末尾";
}
function addBlock() {
  const id = $("addSel").value, spec = BY[id];
  if (!spec) return;
  const p = {};
  const s = itemAt(selPath);
  const target = s && bid(s) === "open" ? (prm(s).flow = prm(s).flow || []) : B.flow;
  const before = target === B.flow ? [B.flow.length] : [...selPath, target.length];
  for (const ps of spec.params) if (ps.type === "out" && ps.required) {
    const used = new Set(listsBefore([1e9])); let n = 1; while (used.has(`${spec.label}${n}`)) n++; p[ps.name] = `${spec.label}${n}`;
  }
  if (id === "open") p.flow = [];
  target.push({ [id]: p });
  selPath = before;
  markDirty(); renderFlow(); renderForm();
}

// ---------------- 参数表单 ----------------
function renderForm() {
  const it = itemAt(selPath);
  $("formBox").classList.toggle("hidden", !it);
  if (!it) return;
  const id = bid(it), spec = BY[id], p = prm(it);
  $("formTitle").textContent = `${selPath.map((x) => x + 1).join(".")} ${spec.label}（${id}）· ${spec.actor === "4b" ? "4b 只回编号" : "程序执行"} · 消耗 ${spec.cost} 次`;
  $("formHelp").textContent = spec.help;
  const lists = listsBefore(selPath);
  const rows = [];
  for (const ps of spec.params) {
    if (ps.type === "flow") continue;
    const v = p[ps.name];
    const dflt = ps.default === null || ps.default === undefined ? "" : (Array.isArray(ps.default) ? ps.default.join("、") : String(ps.default));
    let ctl;
    const attr = `data-p="${esc(ps.name)}" class="${badParam === ps.name ? "bad-param" : ""}"`;
    if (ps.type === "bool") {
      ctl = `<select ${attr}><option value="">默认（${ps.default ? "是" : "否"}）</option><option value="true"${v === true ? " selected" : ""}>是</option><option value="false"${v === false ? " selected" : ""}>否</option></select>`;
    } else if (ps.type === "choice") {
      ctl = `<select ${attr}><option value="">${ps.default ? `默认（${esc(ps.default)}）` : "（请选）"}</option>` +
        ps.choices.map((c) => `<option${v === c ? " selected" : ""}>${esc(c)}</option>`).join("") + "</select>";
    } else if (ps.type === "listref") {
      const opts = [...lists];
      if (v && !opts.includes(v)) opts.push(v);
      ctl = `<select ${attr}><option value="">（选一张清单）</option>` + opts.map((o) => `<option value="${esc(o)}"${o === v ? " selected" : ""}>${esc(o)}` +
        `${REG.builtin_lists.includes(o) ? "（内置）" : lists.includes(o) ? "" : "（这一步之前还没有产出）"}</option>`).join("") + "</select>";
    } else if (ps.type === "int" || ps.type === "float") {
      ctl = `<input type="number" ${attr} value="${v ?? ""}" placeholder="${esc(dflt)}"${ps.min != null ? ` min="${ps.min}"` : ""}${ps.max != null ? ` max="${ps.max}"` : ""}${ps.type === "float" ? ' step="0.05"' : ""}>`;
    } else if (ps.type === "map") {
      const text = Object.entries(v || {}).map(([a, b]) => `${a}: ${b}`).join("\n");
      ctl = `<textarea rows="3" ${attr} placeholder="每行一条：甲: 乙">${esc(text)}</textarea>`;
    } else if (ps.type === "list") {
      ctl = `<input type="text" ${attr} value="${esc((v || []).join("、"))}" placeholder="${esc(dflt || "用逗号或顿号分隔")}">`;
    } else {
      ctl = `<input type="text" ${attr} value="${esc(v ?? "")}" placeholder="${esc(dflt)}">`;
    }
    rows.push(`<label class="${ps.required ? "req" : ""}">${esc(ps.label)}</label><div>${ctl}</div>` +
      (ps.help ? `<div class="help">${esc(ps.name)}：${esc(ps.help)}</div>` : `<div class="help">${esc(ps.name)}</div>`));
  }
  $("form").innerHTML = `<div class="f">${rows.join("")}</div>`;
  $("form").querySelectorAll("[data-p]").forEach((el) => el.addEventListener("change", () => {
    const ps = spec.params.find((x) => x.name === el.dataset.p);
    let v = el.value;
    if (ps.type === "bool") v = v === "" ? undefined : v === "true";
    else if (ps.type === "int") v = v === "" ? undefined : parseInt(v, 10);
    else if (ps.type === "float") v = v === "" ? undefined : parseFloat(v);
    else if (ps.type === "list") v = splitList(v);
    else if (ps.type === "map") {
      const m = {};
      for (const line of v.split(/\n/)) { const k = line.split(/[:：=→]/); if (k.length >= 2 && k[0].trim()) m[k[0].trim()] = k.slice(1).join(":").trim(); }
      v = Object.keys(m).length ? m : undefined;
    } else if (v === "") v = undefined;
    if (v === undefined || (Array.isArray(v) && !v.length)) delete p[ps.name]; else p[ps.name] = v;
    badParam = null; markDirty(); renderFlow();
    if (ps.type === "out") renderForm();
  }));
}

// ---------------- 校验、问题 ----------------
let vTimer = null;
function scheduleValidate() { clearTimeout(vTimer); vTimer = setTimeout(validate, 700); }
function currentText() { return textMode ? $("yaml").value : toYaml(); }
async function validate() {
  try {
    const r = await api("POST", "/api/books/validate", { text: currentText() });
    problems = r.problems; renderProblems(); renderStatus(r.status); renderFlow();
    return r;
  } catch (e) { msg(e.message, true); return null; }
}
function renderProblems() {
  $("probCount").textContent = problems.length ? `${problems.length} 条` : "";
  $("problems").className = "";
  $("problems").innerHTML = problems.length ? problems.map((p, i) => `<li data-i="${i}"><span class="where">${esc([p.step, p.block, p.param].filter(Boolean).join(" / "))}</span>：${esc(p.why)}</li>`).join("")
    : `<li class="ok">没有问题，可以运行</li>`;
  $("problems").querySelectorAll("li[data-i]").forEach((li) => li.addEventListener("click", () => gotoProblem(problems[+li.dataset.i])));
}
function gotoProblem(p) {
  const m = p.step.match(/^flow((?:\[\d+\](?:\.open\.flow)?)+)$/);
  if (m) {
    if (textMode) toForm();
    selPath = [...p.step.matchAll(/\[(\d+)\]/g)].map((x) => +x[1]);
    badParam = p.param; renderFlow(); renderForm();
    $("formBox").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const where = { groups: "groups", start: "fStart", limits: "lBudget", output: "oType", flow: "flow" }[p.step.split("[")[0]] || "params";
  $(where).scrollIntoView({ behavior: "smooth", block: "center" });
}

// ---------------- 打开、新建、导入 ----------------
async function loadList() {
  const rows = await api("GET", "/api/books");
  $("openSel").innerHTML = `<option value="">打开…</option>` + rows.map((r) => `<option value="${esc(r.name)}">${esc(r.title)}（${esc(r.name)}）${r.status !== "ok" ? " · " + (STATUS[r.status] || r.status) : ""}</option>`).join("");
}
async function load(name) {
  const g = await api("GET", `/api/books/${encodeURIComponent(name)}`);
  cur = { name: g.name, rev: g.rev, header: headerOf(g.text) };
  if (g.book) { B = fromBook(g.book); B.name = B.name || g.name; textMode && setText(false); }
  else { B = blank(); setText(true, g.text); }
  dirty = false; selPath = null; problems = g.problems || [];
  renderAll(); renderProblems(); renderStatus(g.status);
  $("conflictBar").classList.add("hidden");
  history.replaceState(null, "", `/build?name=${encodeURIComponent(g.name)}`);
  msg(`已打开 tasks/${g.name}.yaml`);
}
function confirmDiscard(then) {
  if (!dirty) return then();
  modal("放弃未保存的改动？", `<p>当前任务书有改动还没保存。</p>`, [["放弃改动", then, "danger"], ["取消", null]]);
}
function newBlank() {
  B = blank(); cur = { name: null, rev: null, header: "" }; dirty = false; selPath = null; problems = [];
  if (textMode) setText(false);
  renderAll(); renderProblems(); renderStatus("new"); history.replaceState(null, "", "/build"); msg("新建空白任务书");
}
async function importText(text) {
  try {
    const r = await api("POST", "/api/books/import", { text });
    cur = { name: null, rev: null, header: headerOf(r.text) };
    if (r.book) { B = fromBook(r.book); if (textMode) setText(false); }
    else { B = blank(); setText(true, r.text); }
    dirty = true; selPath = null; problems = r.problems;
    renderAll(); renderProblems(); renderStatus(r.status);
    msg(r.legacy ? "已导入上一章的旧格式：结构已转换，条件在“说明”里，需要用积木改写节点流程。还没保存。" : "已导入（还没保存）");
    closeModal();
  } catch (e) { msg(e.message, true); }
}
function importDialog() {
  modal("导入任务书", `<p>选一个本机文件，或把任务书文本（例如 AI 按指导提示词写的）粘贴到下面。</p>
    <p><button id="mPick">选择文件…</button></p><textarea id="mPaste" placeholder="粘贴任务书文本"></textarea>`,
  [["导入粘贴的文本", () => importText($("mPaste").value), "primary", true], ["取消", null]]);
  $("mPick").addEventListener("click", () => $("fileIn").click());
}
$("fileIn").addEventListener("change", async () => {
  const f = $("fileIn").files[0]; if (!f) return;
  importText(await f.text()); $("fileIn").value = "";
});

// ---------------- 保存 ----------------
async function save(asName) {
  const name = (asName || $("fName").value || "").trim();
  if (!name) { msg("请先填名称（英文或拼音）", true); $("fName").focus(); return; }
  B.name = name; if (!textMode) $("fName").value = name;
  const saveAs = !!asName || !cur.rev || cur.name !== name;
  try {
    const r = await api("POST", "/api/books/save", { name, text: currentText(), rev: saveAs ? null : cur.rev, save_as: saveAs });
    cur = { ...cur, name, rev: r.rev }; dirty = false; problems = r.problems;
    renderProblems(); renderStatus(r.status); loadList();
    $("conflictBar").classList.add("hidden");
    history.replaceState(null, "", `/build?name=${encodeURIComponent(name)}`);
    msg(`已保存 tasks/${name}.yaml` + (saveAs ? "" : "（上一版已备份）") + (r.status !== "ok" ? "：有问题，先存为草稿" : ""));
    closeModal();
  } catch (e) {
    if (e.status === 409 && /另一端/.test(e.message)) { $("conflictBar").classList.remove("hidden"); msg(e.message, true); }
    else msg(e.message, true);
  }
}
function saveAsDialog() {
  modal("另存为", `<p>新任务书的名称（英文或拼音，只用字母、数字、下划线、连字符）：</p><input id="mName" value="${esc((B.name || "") + "_2")}" style="width:100%">`,
    [["保存", () => save($("mName").value.trim()), "primary", true], ["取消", null]]);
}

// ---------------- 文本模式 ----------------
function setText(on, text) {
  textMode = on;
  $("formView").classList.toggle("hidden", on); $("textView").classList.toggle("hidden", !on);
  $("bText").textContent = on ? "表单" : "文本";
  if (on) $("yaml").value = text !== undefined ? text : toYaml();
}
async function toForm() {
  const r = await api("POST", "/api/books/validate", { text: $("yaml").value }).catch((e) => { msg(e.message, true); return null; });
  if (!r) return;
  problems = r.problems; renderProblems(); renderStatus(r.status);
  if (!r.book) { msg("文本读不懂，先按问题改好再回到表单", true); return; }
  cur.header = headerOf($("yaml").value);
  B = fromBook(r.book); selPath = null; setText(false); renderAll(); renderStatus(r.status);
}
$("yaml").addEventListener("input", () => { dirty = true; renderStatus(); scheduleValidate(); });

// ---------------- 预览 ----------------
async function doPreview() {
  try {
    const r = await api("POST", "/api/books/preview", { text: currentText() });
    const groups = B.groups.filter(([k]) => k).map(([k, v]) => `<li>${esc(k)}：${esc(v.join("、"))}</li>`).join("");
    $("previewOut").innerHTML = `<p class="small">不加任何新节点时要处理 <b>${r.order.length}</b> 个节点（每个节点按流程消耗次数）。</p>` +
      `<ul class="small">${groups}</ul><p class="small"><b>处理顺序</b>：${r.order.map((x) => esc(x.label)).join(" → ")}</p>`;
    $("previewBox").classList.remove("hidden"); $("previewBox").scrollIntoView({ behavior: "smooth" });
  } catch (e) { msg(e.message, true); }
}

// ---------------- 试跑 ----------------
let trialTimer = null, trialRun = null;
async function startTrial() {
  const v = await validate();
  if (!v || v.status !== "ok") { msg("有问题的任务书不能试跑：先按“校验问题”改好", true); return; }
  try {
    await api("POST", "/api/books/run", { text: currentText(), name: B.name || "draft", trial: true, budget: +$("trialN").value || null });
    trialRun = null; $("trialCards").innerHTML = ""; $("trialBox").classList.remove("hidden");
    $("trialState").textContent = "启动中…"; $("trialBox").scrollIntoView({ behavior: "smooth" });
    clearInterval(trialTimer); trialTimer = setInterval(pollTrial, 1500); pollTrial();
  } catch (e) { msg(e.message, true); }
}
async function getState() { const r = await fetch("/api/state", { headers: TOKEN ? { "X-Panel-Token": TOKEN } : {} }); return r.ok ? r.json() : null; }
async function pollTrial() {
  const st = await getState().catch(() => null);
  const job = st && st.job;
  if (!job) return;
  const run = (job.progress && job.progress.run) || (job.result && job.result.run);
  if (run) trialRun = run;
  const p = job.progress || {};
  $("trialState").textContent = `${st.busy ? "运行中" : "已结束"}：已用 ${p.used ?? 0} / ${p.budget ?? "?"} 次` + (st.pause ? `（${st.pause.reason === "handoff" ? "需要人工" : "已暂停"}）` : "");
  $("bTrialStop").disabled = !st.busy;
  if (trialRun) {
    const steps = await api("GET", `/api/books/steps?run=${encodeURIComponent(trialRun)}`).catch(() => null);
    if (steps) renderCards(steps);
  }
  if (!st.busy) { clearInterval(trialTimer); trialTimer = null; }
}
function pills(arr, cls, f) { return (arr || []).map((x) => `<span class="pill ${cls}">${esc(f(x))}</span>`).join(""); }
function blockDetail(b) {
  const out = b.out || [], lines = [];
  if (b.url) lines.push(`网址：${esc(b.url)}`);
  if (out.length) lines.push(`产出：${pills(out, "", (x) => x.text + (x.element ? ` [${x.element}]` : ""))}`);
  if (b.picked && Array.isArray(b.picked)) lines.push(`4b 选的编号：${esc(b.picked.map((s) => `第${s.screen}屏 ${(s.numbers || []).join(",") || "无"}`).join("；"))}`);
  else if (b.picked) lines.push(`4b 选的编号：${esc(JSON.stringify(b.picked))}`);
  if (b.rejected && b.rejected.length) lines.push(`被拒：${pills(b.rejected, "rej", (x) => `${x.no ?? ""} ${x.text || ""}（${x.why}）`)}`);
  if (b.dropped && b.dropped.length) lines.push(`滤掉：${pills(b.dropped, "drop", (x) => `${x.text}（${x.reason}）`)}`);
  if (b.ratios) lines.push(`比例：${esc(b.ratios.map((r) => `${r.word} ${Math.round(r.ratio * 100)}%`).join("、"))}`);
  if (b.opened) lines.push(`打开：${esc(b.opened.map((o) => `第${o.n}个「${o.target}」${o.how || ""}${o.error ? " " + o.error : ""}${o.back ? " 已返回" : ""}`).join("；"))}`);
  if (b.op) lines.push(`改树：${esc(b.op.op)} ${esc((b.op.words || []).join("、"))}`);
  if (b.missing && b.missing.length) lines.push(`未找到：${esc(b.missing.join("、"))}`);
  return lines.map((l) => `<div class="kv">${l}</div>`).join("");
}
function renderCards(steps) {
  $("trialCards").innerHTML = steps.map((s, i) => {
    const shots = s.blocks.flatMap((b) => b.shot_urls || []);
    const head = `第 ${i + 1} 个节点：<b>${esc(s.word)}</b>${s.key ? `（所在键 ${esc(s.key)}）` : ""} · 消耗 ${s.cost}` +
      (s.discarded ? ` · <span class="warn">${esc(s.discarded)}</span>` : "") + (s.stop ? ` · 停：${esc(s.stop)}` : "");
    const ops = (s.applied || []).map((a) => `${a.op} ${a.words.join("、")}${a.new_terminal ? "（末端）" : ""}`).join("；");
    const rej = (s.rejected || []).map((r) => `${r.word ?? ""}：${r.rejected}`).join("；");
    return `<div class="card"><div>${head}</div>
      ${shots.length ? `<div class="shots">${shots.map((u) => `<a href="${esc(u)}" target="_blank"><img src="${esc(u)}" loading="lazy" alt=""></a>`).join("")}</div>` : ""}
      ${s.blocks.map((b) => `<details><summary>${esc(b.step)} ${esc(b.label || b.block)}` +
        `${b.out ? ` → ${b.out.length} 条` : ""}${b.error ? ` <span class="bad">出错：${esc(b.error)}</span>` : ""}${b.note ? ` <span class="muted">${esc(b.note)}</span>` : ""}` +
        `${b.skipped ? ` <span class="warn">${esc(b.skipped)}</span>` : ""}</summary>${blockDetail(b)}</details>`).join("")}
      <div class="tree-ops">树的变化：${esc(ops || "没有改树（节点成为叶子）")}${rej ? `；被拒：${esc(rej)}` : ""}</div></div>`;
  }).join("") || `<p class="muted small">（还没有记录）</p>`;
}

// ---------------- 指导提示词 ----------------
async function guideDialog() {
  try {
    const text = await api("GET", "/api/books/guide");
    modal("指导提示词（交给任意 AI，让它按格式写任务书）", `<textarea id="mGuide" readonly>${esc(text)}</textarea>`,
      [["复制", async () => {
        try { await navigator.clipboard.writeText(text); msg("已复制到剪贴板"); }
        catch (e) { $("mGuide").select(); document.execCommand("copy"); msg("已复制"); }
      }, "primary", true],
      ["下载 .md", () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
        a.download = "任务书写作指南.md"; document.body.appendChild(a); a.click(); a.remove(); }, "", true],
      ["关闭", null]]);
  } catch (e) { msg(e.message, true); }
}

// ---------------- 弹层 ----------------
function modal(title, body, buttons) {
  $("mTitle").textContent = title; $("mBody").innerHTML = body;
  $("mButtons").innerHTML = buttons.map(([t, , cls], i) => `<button data-i="${i}" class="${cls || ""}">${esc(t)}</button>`).join("");
  $("mButtons").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const [, fn, , keep] = buttons[+b.dataset.i];
    if (!keep) closeModal();
    if (fn) fn();
  }));
  $("modal").classList.remove("hidden");
}
function closeModal() { $("modal").classList.add("hidden"); }

// ---------------- 事件 ----------------
$("fName").addEventListener("change", () => { B.name = $("fName").value.trim(); markDirty(); });
$("fTitle").addEventListener("change", () => { B.title = $("fTitle").value; markDirty(); });
$("fNote").addEventListener("change", () => { B.note = $("fNote").value; markDirty(); });
$("bAddParam").addEventListener("click", () => { B.params.push({ name: "", type: "text", default: "", label: "" }); renderParams(); });
$("bAddGroup").addEventListener("click", () => { B.groups.push(["", []]); renderGroups(); });
$("fStart").addEventListener("change", () => { B.start = $("fStart").value; markDirty(); });
for (const [id, k] of [["lBudget", "budget"], ["lTabs", "keep_tabs"], ["lTrial", "trial_budget"]])
  $(id).addEventListener("change", () => { const v = $(id).value; if (v === "") delete B.limits[k]; else B.limits[k] = parseInt(v, 10); markDirty(); });
$("oType").addEventListener("change", () => {
  B.output = $("oType").value === "ranked" ? { type: "ranked", sort: ["价位:asc", "评分:desc"], n: 3, group_by: "key", unknown: "last" } : { type: "leaves" };
  renderOutput(); markDirty();
});
$("oSort").addEventListener("change", () => { B.output.sort = splitList($("oSort").value.replace(/、/g, ",")); markDirty(); });
$("oN").addEventListener("change", () => { B.output.n = parseInt($("oN").value, 10) || 3; markDirty(); });
$("oGroup").addEventListener("change", () => { if ($("oGroup").checked) B.output.group_by = "key"; else delete B.output.group_by; markDirty(); });
$("oUnknown").addEventListener("change", () => { B.output.unknown = $("oUnknown").value; markDirty(); });
$("bAdd").addEventListener("click", addBlock);
$("bValidate").addEventListener("click", async () => { const r = await validate(); if (r) msg(r.problems.length ? `有 ${r.problems.length} 条问题` : "校验通过"); });
$("bPreview").addEventListener("click", doPreview);
$("bTrial").addEventListener("click", startTrial);
$("bTrialStop").addEventListener("click", () => api("POST", "/api/jobs/stop").catch((e) => msg(e.message, true)));
$("bCloseTrial").addEventListener("click", () => $("trialBox").classList.add("hidden"));
$("bClosePreview").addEventListener("click", () => $("previewBox").classList.add("hidden"));
$("bSave").addEventListener("click", () => save());
$("bSaveAs").addEventListener("click", saveAsDialog);
$("bConflictSaveAs").addEventListener("click", saveAsDialog);
$("bReload").addEventListener("click", () => cur.name && load(cur.name).catch((e) => msg(e.message, true)));
$("bNew").addEventListener("click", () => confirmDiscard(newBlank));
$("bImport").addEventListener("click", importDialog);
$("bGuide").addEventListener("click", guideDialog);
$("bText").addEventListener("click", () => (textMode ? toForm() : setText(true)));
$("bTextApply").addEventListener("click", toForm);
$("openSel").addEventListener("change", () => { const n = $("openSel").value; $("openSel").value = ""; if (n) confirmDiscard(() => load(n).catch((e) => msg(e.message, true))); });
window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

// ---------------- 启动 ----------------
(async function init() {
  try {
    REG = await api("GET", "/api/books/blocks");
    BY = Object.fromEntries(REG.blocks.map((b) => [b.id, b]));
    $("addSel").innerHTML = REG.blocks.map((b) => `<option value="${b.id}">${esc(b.label)}（${b.id}）${b.actor === "4b" ? " · 4b" : ""}</option>`).join("");
    await loadList();
    const name = new URLSearchParams(location.search).get("name");
    if (name) await load(name); else newBlank();
  } catch (e) { msg(e.message, true); }
})();
