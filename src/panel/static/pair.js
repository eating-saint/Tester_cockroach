// 配对页：读出 # 后的配对码（不进服务器日志、不随 Referer 外泄），立刻从地址栏清掉，再提交兑换设备凭证。
"use strict";
(async function () {
  const msg = document.getElementById("msg");
  const code = new URLSearchParams(location.hash.slice(1)).get("c");
  if (location.hash) history.replaceState(null, "", location.pathname);
  if (!code) {
    const need = new URLSearchParams(location.search).get("need");
    msg.innerHTML = need ? "这台设备还没有配对，或凭证已失效（例如公网隧道换了地址、在电脑上被吊销）。<br>请在电脑面板首页的“远程访问”卡片扫码配对。"
      : "请用平板扫电脑面板上“远程访问”卡片里的二维码。";
    return;
  }
  try {
    const r = await fetch("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, ua: navigator.userAgent }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok) throw new Error(j.error || r.statusText);
    msg.textContent = `配对成功：${j.data.name}。正在打开……`;
    setTimeout(() => location.replace("/m"), 600);
  } catch (e) {
    msg.className = "bad";
    msg.textContent = `配对失败：${e.message}。请在电脑上刷新二维码再扫。`;
  }
})();
