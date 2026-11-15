// 人眼比对页（ver1 F9）：左边标注图，右边逐题作答；选号题在图上点框。
"use strict";
const TOKEN = document.querySelector('meta[name="panel-token"]').content;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(method, path, body) {
  const opt = { method, headers: { "X-Panel-Token": TOKEN } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let j = null;
  try { j = await r.json(); } catch (e) { j = { ok: r.ok }; }
  if (!r.ok || j.ok === false) { const e = new Error((j && j.error) || r.status); e.body = j; throw e; }
  return j;
}

let SETS = [], SET = null, PAGES = [], IDX = 0, INFO = null, ANS = {}, PICK = null, saveTimer = null;

function line(msg, bad) { const el = $("jobLine"); el.textContent = msg; el.className = bad ? "bad" : "muted"; }

async function loadSets(keep) {
  SETS = (await api("GET", "/api/compare/sets")).data;
  const sel = $("setSel");
  const cur = keep || sel.value || (SETS[0] && SETS[0].name);
  sel.innerHTML = SETS.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}（${s.pages.length} 页）</option>`).join("");
  if (cur) sel.value = cur;
  SET = SETS.find((s) => s.name === sel.value) || null;
  PAGES = SET ? SET.pages : [];
  renderPageList();
}

function renderPageList() {
  $("pageList").innerHTML = PAGES.map((p, i) => {
    const st = !p.captured ? "未采集" : `已答 ${p.answered}/${p.questions}` + (p.models.length ? `，模型 ${p.models.join("、")}` : "");
    return `<li data-i="${i}" class="${i === IDX ? "cur" : ""}">${esc(p.id)}<span class="st">${esc(st)}</span></li>`;
  }).join("");
}

async function openPage(i) {
  if (!PAGES.length) return;
  IDX = Math.max(0, Math.min(PAGES.length - 1, i));
  renderPageList();
  const p = PAGES[IDX];
  $("pageTitle").textContent = `${IDX + 1} / ${PAGES.length}　${p.id}`;
  $("hl").style.display = "none";
  if (!p.captured) {
    INFO = null; $("shot").removeAttribute("src");
    $("qList").innerHTML = `<p class="muted">这一页还没有采集。点上方"采集截图"。</p>`;
    return;
  }
  INFO = (await api("GET", `/api/compare/page?set=${encodeURIComponent(SET.name)}&page=${encodeURIComponent(p.id)}`)).data;
  ANS = { ...INFO.human };
  $("shot").src = INFO.images["annotated.png"] + "?t=" + Date.now();
  renderQuestions();
}

function renderQuestions() {
  const out = [];
  for (const q of INFO.questions) {
    const v = ANS[q.id];
    let body = "";
    if (q.type === "select") {
      const shown = v === undefined ? "未答" : (v === 0 ? "无" : `${v} 号`);
      body = `<div class="ans"><span class="val" id="v_${q.id}">${esc(shown)}</span>
        <button type="button" data-pick="${q.id}">在图上选</button><button type="button" data-none="${q.id}">无</button></div>`;
    } else if (q.type === "text") {
      body = `<input type="text" data-text="${q.id}" value="${esc(v ?? "")}" placeholder="照抄框里的文字">`;
    } else {
      const set = new Set(v || []);
      body = q.kinds.map((k) => `<label class="kind"><input type="checkbox" data-area="${q.id}" value="${esc(k)}" ${set.has(k) ? "checked" : ""}>${esc(k)}</label>`).join("");
    }
    out.push(`<div class="q"><div class="t">${esc(q.text)}</div>${body}</div>`);
  }
  $("qList").innerHTML = out.join("");
}

function scheduleSave() {
  clearTimeout(saveTimer);
  $("saveLine").textContent = "保存中……";
  saveTimer = setTimeout(async () => {
    try {
      await api("POST", "/api/compare/answer", { set: SET.name, page: PAGES[IDX].id, answers: ANS });
      $("saveLine").textContent = "已保存";
      const p = PAGES[IDX]; p.answered = INFO.questions.filter((q) => ANS[q.id] !== undefined).length; renderPageList();
    } catch (e) { $("saveLine").textContent = "保存失败：" + e.message; }
  }, 300);
}

function hitTest(ev) {
  const img = $("shot");
  const r = img.getBoundingClientRect();
  const sx = img.naturalWidth / r.width, sy = img.naturalHeight / r.height;
  const x = (ev.clientX - r.left) * sx, y = (ev.clientY - r.top) * sy;
  const hits = INFO.elements.filter((e) => x >= e.box.x && x <= e.box.x + e.box.w && y >= e.box.y && y <= e.box.y + e.box.h);
  if (!hits.length) return null;
  hits.sort((a, b) => a.box.w * a.box.h - b.box.w * b.box.h);
  return { el: hits[0], sx, sy };
}

function highlight(h) {
  const hl = $("hl");
  if (!h) { hl.style.display = "none"; return; }
  const b = h.el.box;
  Object.assign(hl.style, { display: "block", left: b.x / h.sx + "px", top: b.y / h.sy + "px", width: b.w / h.sx + "px", height: b.h / h.sy + "px" });
}

$("shot").addEventListener("mousemove", (ev) => { if (PICK && INFO) highlight(hitTest(ev)); });
$("shot").addEventListener("click", (ev) => {
  if (!PICK || !INFO) return;
  const h = hitTest(ev);
  if (!h) return;
  ANS[PICK] = h.el.no;
  $("v_" + PICK).textContent = `${h.el.no} 号`;
  PICK = null; $("imgWrap").classList.remove("picking"); highlight(null);
  scheduleSave();
});
$("qList").addEventListener("click", (ev) => {
  const p = ev.target.dataset.pick, n = ev.target.dataset.none;
  if (p) { PICK = p; $("imgWrap").classList.add("picking"); $("saveLine").textContent = "在左边图上点一个框"; }
  if (n) { ANS[n] = 0; $("v_" + n).textContent = "无"; scheduleSave(); }
});
$("qList").addEventListener("input", (ev) => {
  const t = ev.target.dataset.text, a = ev.target.dataset.area;
  if (t) { ANS[t] = ev.target.value; scheduleSave(); }
  if (a) { ANS[a] = [...document.querySelectorAll(`[data-area="${a}"]:checked`)].map((x) => x.value); scheduleSave(); }
});
$("pageList").addEventListener("click", (ev) => { const li = ev.target.closest("li"); if (li) openPage(+li.dataset.i); });
$("zoom").onclick = () => {
  const on = $("imgWrap").classList.toggle("actual");
  $("zoom").textContent = on ? "适应宽度" : "原尺寸";
  highlight(null);
};
$("prev").onclick = () => openPage(IDX - 1);
$("next").onclick = () => openPage(IDX + 1);
$("setSel").onchange = async () => { IDX = 0; await loadSets($("setSel").value); openPage(0); };

async function watchJob() {
  for (;;) {
    let st;
    try { st = await (await fetch("/api/state", { headers: { "X-Panel-Token": TOKEN } })).json(); } catch (e) { break; }
    const job = st.job;
    if (!st.busy) {
      if (job) line(`${job.title}：${job.result ? job.result.reason : job.state}`, job.state === "failed");
      break;
    }
    const pr = job && job.progress ? `${job.progress.done || 0}/${job.progress.total || "?"}　${job.progress.line || ""}` : "";
    line(`正在运行：${job ? job.title : ""}　${pr}`);
    await new Promise((r) => setTimeout(r, 1000));
  }
  await loadSets(SET && SET.name);
  openPage(IDX);
}

$("btnCapture").onclick = async () => {
  try { await api("POST", "/api/compare/capture", { set: SET.name }); watchJob(); }
  catch (e) { line("没能开始采集：" + e.message, true); }
};
$("btnModel").onclick = async () => {
  try { await api("POST", "/api/compare/run_model", { set: SET.name, model: $("modelName").value.trim() || null }); watchJob(); }
  catch (e) { line("没能开始模型作答：" + e.message, true); }
};

function pct(v) { return v == null ? "-" : Math.round(v * 100) + "%"; }
$("btnReport").onclick = async () => {
  try {
    const r = (await api("GET", `/api/compare/report?set=${encodeURIComponent(SET.name)}`)).data;
    const s = r.report.summary;
    $("reportName").textContent = `${r.dir}（模型 ${r.report.model}；人已作答 ${s.answered ?? "-"} / ${s.pages} 页，未答的题不计入）`;
    let h = `<table><tr><th>项目</th><th>题数</th><th>结果</th></tr>
      <tr><td>选号一致率（人 vs 模型）</td><td>${s.select.n}</td><td>${pct(s.select.agree_rate)}</td></tr>
      <tr><td>文字读取一致率（人 vs 模型）</td><td>${s.text.n}</td><td>${pct(s.text.human_model)}，平均相似度 ${s.text.avg_similarity ?? "-"}</td></tr>
      <tr><td>文字：人 vs DOM</td><td>${s.text.n}</td><td>${pct(s.text.human_dom)}</td></tr>
      <tr><td>文字：模型 vs DOM</td><td>${s.text.n}</td><td>${pct(s.text.model_dom)}</td></tr>
      <tr><td>区块识别：模型漏报 / 错报</td><td>${s.areas.n} 页</td><td>${s.areas.missed} / ${s.areas.false}</td></tr></table>`;
    const kinds = Object.entries(s.areas.per_kind || {});
    if (kinds.length) h += `<table><tr><th>区块类型</th><th>漏报</th><th>错报</th></tr>${kinds.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.missed}</td><td>${v.false}</td></tr>`).join("")}</table>`;
    if (s.mismatches.length) {
      h += `<h4>不一致的题（${s.mismatches.length}）</h4><table><tr><th>页面</th><th>题</th><th>人</th><th>模型</th><th>DOM</th></tr>` +
        s.mismatches.map((m) => `<tr><td>${esc(m.page)}</td><td>${esc(m.question)}</td><td>${esc(JSON.stringify(m.human))}</td><td>${esc(JSON.stringify(m.model))}</td><td>${esc(m.dom || "")}</td></tr>`).join("") + "</table>";
    }
    $("reportBody").innerHTML = h;
    $("reportView").hidden = false;
  } catch (e) { line("没有报告：" + e.message, true); }
};
$("closeReport").onclick = () => { $("reportView").hidden = true; };

(async () => { try { await loadSets(); openPage(0); } catch (e) { line("加载失败：" + e.message, true); } })();
