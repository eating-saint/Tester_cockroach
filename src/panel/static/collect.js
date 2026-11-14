"use strict";
// 本地轨迹采集页（F15 本地来源，K8–K10）。纯前端：生成仿真实网站布局 → 出题 → 记录 pointermove → 预校验 → 提交。
const TOKEN = document.querySelector('meta[name="panel-token"]').content;
const $ = (id) => document.getElementById(id);
const DIR_NAMES = ["右", "右下", "下", "左下", "左", "左上", "上", "右上"];
const PER_CELL = 5;          // 每个设备每个方向的目标条数
const MAX_MS = 5000, MIN_POINTS = 8, MIN_DIST = 150, MAX_DIST = 600, ANGLE_TOL = 15;
const REASONS = {
  miss: "没点中橙色目标", left_area: "移出了采集区", too_slow: "超过 5 秒", too_few: "轨迹点太少（移动太快或没移动）",
  duplicate: "和已有样本完全相同",
};
let AREA_W = 1100, AREA_H = 660;   // 随窗口大小调整（上限 1100×660），见 fitArea()

// ---------------- 工具 ----------------
async function api(method, path, body, raw) {
  const opt = { method, headers: { "X-Panel-Token": TOKEN } };
  if (body !== undefined) {
    if (raw) { opt.body = body; opt.headers["Content-Type"] = "application/json"; }
    else { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  }
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
function msg(text, kind) { const m = $("msg"); m.textContent = text; m.className = "msg " + (kind || ""); }
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function directionOf(dx, dy) {   // 与 hands.humanize.direction_of 相同：0=右，顺时针
  const k = Math.round(Math.atan2(dy, dx) / (Math.PI / 4));
  return ((k % 8) + 8) % 8;
}
const center = (b) => [b.x + b.width / 2, b.y + b.height / 2];
const inBox = (b, x, y, pad) => x >= b.x - (pad || 0) && x <= b.x + b.width + (pad || 0) &&
  y >= b.y - (pad || 0) && y <= b.y + b.height + (pad || 0);

// ---------------- 布局：仿真实网站 ----------------
const NAV = ["首页", "分类", "特价", "新品", "会员", "帮助", "订单", "收藏夹"];
const CATS = ["手机数码", "电脑办公", "家用电器", "服饰鞋包", "生鲜食品", "图书音像", "运动户外", "美妆个护", "母婴玩具", "家居家装"];
const GOODS = ["无线耳机", "保温杯", "机械键盘", "运动鞋", "台灯", "双肩包", "电饭煲", "充电宝", "牛仔裤", "咖啡豆", "显示器", "抱枕"];

function makeLayout(seed, W, H) {
  const r = mulberry32(seed);
  const ri = (a, b) => a + Math.floor(r() * (b - a + 1));
  const rf = (a, b) => a + r() * (b - a);
  const els = [];
  const add = (cls, x, y, w, h, text, click) => {
    const e = { cls, box: { x: Math.round(x), y: Math.round(y), width: Math.round(w), height: Math.round(h) }, text: text || "", click: !!click };
    els.push(e); return e;
  };
  // 顶部导航栏
  const navH = ri(48, 60);
  add("navbar", 0, 0, W, navH);
  add("logo", 12, (navH - 28) / 2, 90, 28, "好物商城");
  // 搜索框 + 按钮放在导航栏右侧；先定尺寸，导航链接不越过它
  const sH = ri(28, 36), sW = Math.min(ri(180, 300), Math.round(W * 0.25)), bW = ri(48, 76);
  const sx = W - (sW + bW + 30) - ri(0, 40);
  let x = 120 + ri(0, 40);
  const nLinks = ri(5, 7);
  for (let i = 0; i < nLinks; i++) {
    const w = ri(40, 80), h = ri(20, 30);
    if (x + w > sx - 10) break;
    add("nav", x, (navH - h) / 2 + ri(-4, 4), w, h, NAV[i % NAV.length], true);
    x += w + ri(10, 30);
  }
  add("search", sx, (navH - sH) / 2, sW, sH, "搜索商品", true);
  add("btn", sx + sW + 4, (navH - sH) / 2, bW, sH, "搜索", true);
  // 左侧分类
  let y = navH + ri(16, 28);
  const cx = ri(12, 24), nCats = ri(6, 10);
  for (let i = 0; i < nCats && y < H - 90; i++) {
    const h = ri(18, 28);
    add("cat", cx, y, ri(90, 130), h, CATS[(i + seed) % CATS.length], true);
    y += h + ri(4, 14);
  }
  // 商品卡片
  const x0 = 170, y0 = navH + 16, x1 = W - 16, y1 = H - 64;
  const n = ri(3, 8);
  const cols = Math.max(Math.ceil(n / 2), Math.min(n, ri(2, 4)));
  const rows = Math.ceil(n / cols);
  const cw = (x1 - x0) / cols, ch = (y1 - y0) / rows;
  for (let i = 0; i < n; i++) {
    const c = i % cols, rw = Math.floor(i / cols);
    const w = cw * rf(0.7, 0.92), h = ch * rf(0.75, 0.95);
    const px = x0 + c * cw + rf(0, cw - w), py = y0 + rw * ch + rf(0, ch - h);
    add("card", px, py, w, h);
    add("img", px + 8, py + 8, w - 16, h * 0.4);
    add("title", px + 8, py + 12 + h * 0.4, w - 16, 18, GOODS[(i * 7 + seed) % GOODS.length]);
    add("price", px + 8, py + 32 + h * 0.4, 80, 18, "¥" + ri(9, 999));
    const bw = ri(60, 80), bh = ri(24, 36);
    const bx = r() < 0.5 ? px + 8 : px + w - bw - 8;
    add("cart", bx, py + h - bh - 8, bw, bh, "加入购物车", true);
    const fs = ri(16, 24);
    add("fav", px + w - fs - 6, py + 6, fs, fs, "♥", true);
  }
  // 分页
  const nPages = ri(5, 7), ps = ri(24, 40), gap = ri(4, 12);
  const total = nPages * ps + (nPages - 1) * gap;
  let px = Math.min(Math.max((W - total) / 2 + ri(-120, 120), 170), W - total - 10);
  const py = H - 48 + ri(-4, 4);
  for (let i = 0; i < nPages; i++) {
    const t = i === 0 ? "‹" : i === nPages - 1 ? "›" : String(i);
    add("page", px, py, ps, ps, t, true);
    px += ps + gap;
  }
  return els;
}

// 找一对 起点控件 → 目标控件，方向属于 dir（±ANGLE_TOL°）、距离 150–600
function findPair(els, dir, rnd) {
  const cl = els.filter((e) => e.click);
  const pairs = [];
  for (const a of cl) {
    const [ax, ay] = center(a.box);
    for (const b of cl) {
      if (a === b) continue;
      const [bx, by] = center(b.box);
      const dx = bx - ax, dy = by - ay, d = Math.hypot(dx, dy);
      if (d < MIN_DIST || d > MAX_DIST) continue;
      let ang = Math.atan2(dy, dx) * 180 / Math.PI - dir * 45;
      ang = ((ang + 540) % 360) - 180;
      if (Math.abs(ang) <= ANGLE_TOL) pairs.push([a, b, d]);
    }
  }
  if (!pairs.length) return null;
  return pairs[Math.floor(rnd() * pairs.length)];
}

// ---------------- 状态 ----------------
let device = null;
let grid = null;
let els = [];
let layoutSeed = 0;
let queue = [];             // 待出题的方向
let qCount = 0, qTotal = 0;
let fillMode = false;
let q = null;               // 当前题 {dir, start, target}
let rec = null;             // 记录中 {t0, start, points, left, timer}
let sinceLayout = 0;

function count(dev, d) { const c = grid && grid[dev] && grid[dev][d]; return c ? c.local + c.real : 0; }
function needDirs() { return [0, 1, 2, 3, 4, 5, 6, 7].filter((d) => count(device, d) < PER_CELL); }

function renderLayout() {
  const L = $("layout");
  L.innerHTML = "";
  for (const e of els) {
    const d = document.createElement("div");
    d.className = "el " + e.cls;
    const b = e.box;
    d.style.left = b.x + "px"; d.style.top = b.y + "px"; d.style.width = b.width + "px"; d.style.height = b.height + "px";
    d.textContent = e.text;
    e.node = d;
    L.appendChild(d);
  }
}
function fitArea() {
  const left = $("left");
  const top = $("area").getBoundingClientRect().top;
  AREA_W = Math.max(720, Math.min(1100, left.clientWidth - 8));
  AREA_H = Math.max(480, Math.min(660, window.innerHeight - top - 60));
  area.style.width = AREA_W + "px"; area.style.height = AREA_H + "px";
  setupCanvas();
}
function newLayout() {
  if (!rec) fitArea();
  layoutSeed = (Math.random() * 2 ** 31) | 0;
  els = makeLayout(layoutSeed, AREA_W, AREA_H);
  renderLayout();
  sinceLayout = 0;
}
function tag(cls, text, b) {
  const t = document.createElement("div");
  t.className = "tag " + cls; t.textContent = text;
  t.style.left = Math.min(b.x, AREA_W - 90) + "px";
  t.style.top = (b.y > 22 ? b.y - 20 : b.y + b.height + 4) + "px";
  $("layout").appendChild(t);
}
function clearQuestion() {
  document.querySelectorAll(".q-start,.q-target").forEach((n) => n.classList.remove("q-start", "q-target"));
  document.querySelectorAll(".tag").forEach((n) => n.remove());
}

function nextQuestion(retryDir) {
  clearQuestion();
  clearDraw();
  if (retryDir === undefined) {
    // 补采模式下队列空了就按缺口重新排
    if (!queue.length && fillMode) queue = buildFillQueue();
    queue = queue.filter((d) => count(device, d) < PER_CELL);
    if (!queue.length) { stopCollect(fillMode ? "这个设备的 8 个方向都已采满。" : "这一组采完了。", "ok"); return; }
  }
  const dir = retryDir !== undefined ? retryDir : queue[0];
  if (sinceLayout >= 8 && retryDir === undefined) newLayout();
  let pair = null;
  for (let tries = 0; tries < 30 && !pair; tries++) {
    pair = findPair(els, dir, Math.random);
    if (!pair) newLayout();
  }
  if (!pair) { stopCollect("找不到合适的题目，请重试。", "bad"); return; }
  q = { dir, start: pair[0], target: pair[1] };
  pair[0].node.classList.add("q-start"); pair[1].node.classList.add("q-target");
  tag("start", "①先点这里", pair[0].box); tag("target", "②再点这里", pair[1].box);
  if (retryDir === undefined) { qCount++; sinceLayout++; }
  msg(`第 ${qCount}/${qTotal || "?"} 题 · 方向：${DIR_NAMES[dir]} · 先点绿框，再移到橙框点下去`);
  renderGrids();
}

function buildFillQueue() {
  const out = [];
  for (let round = 0; round < PER_CELL; round++)
    for (let d = 0; d < 8; d++) if (count(device, d) + round < PER_CELL) out.push(d);
  return out;
}

function startGroup(fill) {
  if (!device) return;
  fillMode = fill;
  queue = fill ? buildFillQueue() : needDirs();
  qTotal = queue.length; qCount = 0;
  if (!queue.length) { msg("这个设备的 8 个方向都已采满；可以在“样本”里删掉不满意的再补采。", "ok"); return; }
  newLayout();
  setRunning(true);
  nextQuestion();
}
function stopCollect(text, kind) {
  abortRec();
  clearQuestion();
  q = null; queue = [];
  setRunning(false);
  if (text) msg(text, kind);
  renderGrids();
}
function setRunning(on) {
  $("bGroup").disabled = on || !device; $("bFill").disabled = on || !device; $("bStopCollect").disabled = !on;
  document.querySelectorAll("input[name=device]").forEach((x) => { x.disabled = on; });
}

// ---------------- 记录 ----------------
const area = $("area");
function local(e) {
  const r = $("layout").getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}
function abortRec() {
  if (rec && rec.timer) clearTimeout(rec.timer);
  rec = null;
  area.classList.remove("recording");
}
function reject(reason) {
  const dir = q ? q.dir : 0;
  abortRec();
  msg(`作废：${REASONS[reason] || reason}。重做这道题。`, "bad");
  setTimeout(() => { if (q) { nextQuestion(dir); msg(`作废：${REASONS[reason] || reason}。重做这道题（方向：${DIR_NAMES[dir]}）。`, "bad"); } }, 600);
}

area.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  if (!q) return;
  const [x, y] = local(e);
  if (!rec) {
    if (!inBox(q.start.box, x, y, 2)) { msg("请先点绿框“①先点这里”。", "bad"); return; }
    rec = { t0: e.timeStamp, start: [x, y], points: [[x, y, 0]], left: false };
    rec.timer = setTimeout(() => { if (rec) reject("too_slow"); }, MAX_MS + 50);
    area.classList.add("recording");
    msg(`记录中 · 方向：${DIR_NAMES[q.dir]} · 移到橙框点下去`);
    return;
  }
  const t = Math.round(e.timeStamp - rec.t0);
  const last = rec.points[rec.points.length - 1];
  if (last[0] !== x || last[1] !== y) rec.points.push([x, y, Math.max(t, last[2])]);
  finish([x, y]);
});
area.addEventListener("pointermove", (e) => {
  if (!rec) return;
  const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [];
  for (const ev of (evs.length ? evs : [e])) {
    const [x, y] = local(ev);
    const t = Math.round(ev.timeStamp - rec.t0);
    const last = rec.points[rec.points.length - 1];
    if (x === last[0] && y === last[1]) continue;
    rec.points.push([x, y, Math.max(t, last[2])]);
  }
});
area.addEventListener("pointerleave", () => { if (rec) reject("left_area"); });
area.addEventListener("contextmenu", (e) => e.preventDefault());

function precheck(raw) {
  const b = raw.target, [x, y] = raw.end;
  if (!inBox(b, x, y)) return "miss";
  if (raw.left_area) return "left_area";
  if (raw.points[raw.points.length - 1][2] > MAX_MS) return "too_slow";
  if (raw.points.length < MIN_POINTS) return "too_few";
  return null;
}

async function finish(end) {
  const r = rec, cur = q;
  abortRec();
  const raw = {
    device, source: "local", start: r.start, end, target: { ...cur.target.box },
    points: r.points.map(([x, y, t]) => [Math.round(x * 100) / 100, Math.round(y * 100) / 100, t]),
    dpr: window.devicePixelRatio || 1, left_area: r.left,
  };
  const bad = precheck(raw);
  if (bad) { q = cur; reject(bad); return; }
  drawSample(raw);
  try {
    const res = (await api("POST", "/api/samples", raw)).data;
    if (!res.ok) { q = cur; reject(res.reason); return; }
    const dur = raw.points[raw.points.length - 1][2];
    msg(`已保存（方向：${DIR_NAMES[res.direction]}，${dur} ms，${raw.points.length} 个点）`, "ok");
    await loadGrid();
    queue.shift();
    setTimeout(() => { if (q === cur) nextQuestion(); }, 500);
  } catch (err) {
    q = cur; msg("提交失败：" + err.message, "bad");
  }
}

// ---------------- 进度格 ----------------
async function loadGrid() {
  try { grid = (await api("GET", "/api/samples/grid")).data; } catch (e) { grid = null; }
  renderGrids();
}
function renderGrids() {
  if (!grid) { $("grids").textContent = "（读取失败）"; return; }
  const parts = [];
  for (const dev of ["mouse", "touchpad"]) {
    const g = grid[dev];
    let total = 0;
    const cells = DIR_NAMES.map((n, d) => {
      const c = g[d], s = c.local + c.real; total += Math.min(s, PER_CELL);
      const cls = [s >= PER_CELL ? "full" : "", q && dev === device && d === q.dir ? "cur" : ""].join(" ");
      return `<td class="${cls}" title="本地 ${c.local} / 真实 ${c.real}">${c.local}/${c.real}</td>`;
    });
    parts.push(`<table class="grid"><tr><th colspan="8">${dev === "mouse" ? "鼠标" : "触控板"}（${total}/${8 * PER_CELL}）</th></tr>` +
      `<tr>${DIR_NAMES.map((n) => `<th>${n}</th>`).join("")}</tr><tr>${cells.join("")}</tr></table>`);
  }
  $("grids").innerHTML = parts.join("");
}

// ---------------- 画图与回放 ----------------
const cv = $("draw");
function setupCanvas() {
  const dpr = window.devicePixelRatio || 1;
  cv.width = AREA_W * dpr; cv.height = AREA_H * dpr;
  cv.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
}
function clearDraw() { cv.getContext("2d").clearRect(0, 0, AREA_W, AREA_H); }
let anim = null;
function drawSample(s, upto) {
  const c = cv.getContext("2d");
  clearDraw();
  const t = s.target;
  c.strokeStyle = "#2563eb"; c.lineWidth = 2; c.strokeRect(t.x, t.y, t.width, t.height);
  const pts = upto === undefined ? s.points : s.points.filter((p) => p[2] <= upto);
  if (pts.length) {
    c.strokeStyle = "#7c3aed"; c.lineWidth = 2; c.beginPath();
    pts.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
    c.stroke();
    c.fillStyle = "#7c3aed";
    for (const p of pts) { c.beginPath(); c.arc(p[0], p[1], 1.6, 0, 7); c.fill(); }
  }
  c.strokeStyle = "#16a34a"; c.lineWidth = 3; c.beginPath(); c.arc(s.start[0], s.start[1], 7, 0, 7); c.stroke();
  if (upto === undefined || upto >= s.points[s.points.length - 1][2]) {
    c.fillStyle = "#dc2626"; c.beginPath(); c.arc(s.end[0], s.end[1], 4, 0, 7); c.fill();
  }
}
function replay(s) {
  if (anim) cancelAnimationFrame(anim);
  const dur = s.points[s.points.length - 1][2];
  const t0 = performance.now();
  const step = () => {
    const el = performance.now() - t0;
    drawSample(s, el);
    if (el < dur) anim = requestAnimationFrame(step); else { anim = null; drawSample(s); }
  };
  step();
}

// ---------------- 样本列表 ----------------
let selected = null;
async function loadList() {
  const qs = new URLSearchParams();
  if ($("fDevice").value) qs.set("device", $("fDevice").value);
  if ($("fSource").value) qs.set("source", $("fSource").value);
  let rows = [];
  try { rows = (await api("GET", "/api/samples?" + qs)).data; } catch (e) { $("list").textContent = "读取失败"; return; }
  rows.sort((a, b) => (b.created || "").localeCompare(a.created || ""));
  $("list").innerHTML = `<table><tr><th>设备</th><th>来源</th><th>方向</th><th>时长</th><th>点数</th><th>时间</th></tr>` +
    rows.map((s) => `<tr data-id="${s.id}" class="${selected && selected.id === s.id ? "sel" : ""}">` +
      `<td>${s.device === "mouse" ? "鼠标" : "触控板"}</td><td>${s.source === "real" ? "真实" + (s.domain ? "·" + s.domain : "") : "本地"}</td>` +
      `<td>${DIR_NAMES[s.direction]}</td><td>${s.duration_ms} ms</td><td>${s.n_points}</td><td>${(s.created || "").replace("T", " ")}</td></tr>`).join("") +
    `</table><div class="muted small">共 ${rows.length} 条</div>`;
}
$("list").addEventListener("click", async (e) => {
  const tr = e.target.closest("tr[data-id]"); if (!tr) return;
  try { selected = (await api("GET", "/api/samples/" + tr.dataset.id)).data; } catch (err) { return; }
  document.querySelectorAll("#list tr").forEach((x) => x.classList.toggle("sel", x === tr));
  $("bReplay").disabled = false; $("bDelete").disabled = false;
  $("selInfo").textContent = `编号 ${selected.id}`;
  drawSample(selected);
});
$("bReplay").addEventListener("click", () => selected && replay(selected));
$("bDelete").addEventListener("click", async () => {
  if (!selected || !confirm("删除这条样本？")) return;
  await api("DELETE", "/api/samples/" + selected.id);
  selected = null; $("bReplay").disabled = true; $("bDelete").disabled = true; $("selInfo").textContent = "";
  clearDraw(); await loadGrid(); await loadList();
});
$("bRefresh").addEventListener("click", loadList);
$("fDevice").addEventListener("change", loadList);
$("fSource").addEventListener("change", loadList);
$("bExport").addEventListener("click", async () => {
  const r = await fetch("/api/samples/export", { headers: { "X-Panel-Token": TOKEN } });
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "samples_" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".json";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
});
$("bImport").addEventListener("click", async () => {
  const f = $("fileIn").files[0];
  if (!f) { msg("先选择要导入的 JSON 文件。", "bad"); return; }
  const mode = $("impMode").value;
  if (mode === "replace" && !confirm("替换会删除当前库里的全部样本，确定？")) return;
  try {
    const res = (await api("POST", "/api/samples/import?mode=" + mode, await f.arrayBuffer(), true)).data;
    if (!res.ok) { msg("导入失败：" + res.error, "bad"); return; }
    msg(`导入完成：新增 ${res.added} 条，库里共 ${res.total} 条。`, "ok");
  } catch (err) { msg("导入失败：" + err.message, "bad"); }
  await loadGrid(); await loadList();
});

// ---------------- 视图与按钮 ----------------
document.querySelectorAll("button.view").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("button.view").forEach((x) => x.classList.toggle("on", x === b));
  const s = b.dataset.view === "samples";
  $("samplesView").classList.toggle("hidden", !s);
  if (s) { stopCollect("已暂停采集。"); loadList(); } else clearDraw();
}));
document.querySelectorAll("input[name=device]").forEach((x) => x.addEventListener("change", () => {
  device = x.value;
  $("bGroup").disabled = false; $("bFill").disabled = false;
  msg(`设备：${device === "mouse" ? "鼠标" : "触控板"}。点“开始一组”。`);
}));
$("bGroup").addEventListener("click", () => startGroup(false));
$("bFill").addEventListener("click", () => startGroup(true));
$("bStopCollect").addEventListener("click", () => stopCollect("已暂停。"));
window.addEventListener("keydown", (e) => { if (e.key === "Escape" && q) stopCollect("已暂停。"); });
// 面板窗口里的兜底拦截：外链、新窗口、拖放
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]"); if (!a || a.hasAttribute("download")) return;
  if (new URL(a.href, location.href).origin !== location.origin) { e.preventDefault(); msg("已拦截外部链接", "bad"); }
}, true);
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => e.preventDefault());

// ---------------- 自检（K8 验证用） ----------------
window.__selfTest = function (n, w, h) {
  const SW = w || 1100, SH = h || 660;
  const dirs = [0, 0, 0, 0, 0, 0, 0, 0];
  let distOk = true, sizeOk = true, fails = 0, relayouts = 0;
  for (let i = 0; i < n; i++) {
    const dir = i % 8;
    let pair = null, L;
    for (let k = 0; k < 30 && !pair; k++) {
      L = makeLayout(i * 1000 + k + 1, SW, SH);
      pair = findPair(L, dir, mulberry32(i + 7));
      if (!pair) relayouts++;
    }
    if (!pair) { fails++; continue; }
    const [a, b, d] = pair;
    const [ax, ay] = center(a.box), [bx, by] = center(b.box);
    dirs[directionOf(bx - ax, by - ay)]++;
    if (d < MIN_DIST || d > MAX_DIST) distOk = false;
    const m = Math.min(b.box.width, b.box.height);
    if (m < 16 || m > 80) sizeOk = false;
    for (const e of L) if (e.click && (e.box.x < 0 || e.box.y < 0 || e.box.x + e.box.width > SW || e.box.y + e.box.height > SH)) sizeOk = false;
  }
  const res = { n, w: SW, h: SH, dirs, distOk, sizeOk, fails, relayouts };
  console.log("SELFTEST " + JSON.stringify(res));
  return res;
};
// 测试用：读当前题目的框（CSS 坐标，相对视口）
window.__question = function () {
  if (!q) return null;
  const r = $("layout").getBoundingClientRect();
  const abs = (b) => ({ x: b.x + r.left, y: b.y + r.top, width: b.width, height: b.height });
  return { dir: q.dir, start: abs(q.start.box), target: abs(q.target.box), recording: !!rec };
};

newLayout();
loadGrid();
window.addEventListener("resize", () => { if (!q) newLayout(); });
