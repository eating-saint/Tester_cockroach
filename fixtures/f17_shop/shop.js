// F17 仿电商夹具：商品数据与公共渲染。纯前端，不联网。
window.PRODUCTS = [
  {id: 1, name: "Beef Noodle Soup", cat: "面食", price: "$13.50", rating: "4.5", eta: "30 min"},
  {id: 2, name: "Garlic Butter Shrimp", cat: "小吃", price: "$16.20", rating: "4.3", eta: "35 min"},
  {id: 3, name: "Spicy Ramen", cat: "面食", price: "$12.99", rating: "4.7", eta: "25 min"},
  {id: 4, name: "Mango Smoothie", cat: "饮品", price: "$5.80", rating: "4.4", eta: "15 min"},
  {id: 5, name: "Chocolate Lava Cake", cat: "甜点", price: "$7.25", rating: "4.8", eta: "20 min"},
  {id: 6, name: "Chicken Caesar Wrap", cat: "小吃", price: "$9.40", rating: "4.1", eta: "25 min"},
  {id: 7, name: "Iced Matcha Latte", cat: "饮品", price: "$6.10", rating: "4.6", eta: "15 min"},
  {id: 8, name: "Fried Dumplings", cat: "小吃", price: "$8.90", rating: "4.5", eta: "30 min"},
  {id: 9, name: "Tomato Basil Pasta", cat: "面食", price: "$14.00", rating: "4.2", eta: "35 min"},
  {id: 10, name: "Spicy Chicken Wings", cat: "小吃", price: "$11.75", rating: "4.6", eta: "28 min"},
  {id: 11, name: "Lemon Iced Tea", cat: "饮品", price: "$4.50", rating: "4.0", eta: "10 min"},
  {id: 12, name: "Strawberry Cheesecake", cat: "甜点", price: "$7.90", rating: "4.7", eta: "20 min"},
  {id: 13, name: "Pork Bun", cat: "小吃", price: "$6.30", rating: "4.3", eta: "22 min"},
  {id: 14, name: "Udon Bowl", cat: "面食", price: "$12.10", rating: "4.4", eta: "30 min"},
  {id: 15, name: "Vanilla Milkshake", cat: "饮品", price: "$5.95", rating: "4.2", eta: "15 min"},
  {id: 16, name: "Tiramisu Cup", cat: "甜点", price: "$6.80", rating: "4.6", eta: "20 min"},
  {id: 17, name: "Fish Tacos", cat: "小吃", price: "$10.50", rating: "4.4", eta: "32 min"},
  {id: 18, name: "Mushroom Risotto", cat: "面食", price: "$15.30", rating: "4.5", eta: "40 min"},
  {id: 19, name: "Spicy Mapo Tofu", cat: "小吃", price: "$11.20", rating: "4.8", eta: "27 min"},
  {id: 20, name: "Peach Oolong Tea", cat: "饮品", price: "$5.20", rating: "4.3", eta: "12 min"},
  {id: 21, name: "Egg Tart", cat: "甜点", price: "$3.90", rating: "4.5", eta: "18 min"},
  {id: 22, name: "Teriyaki Salmon", cat: "小吃", price: "$17.60", rating: "4.6", eta: "35 min"},
  {id: 23, name: "Seafood Fried Rice", cat: "面食", price: "$13.80", rating: "4.4", eta: "30 min"},
  {id: 24, name: "Coconut Pudding", cat: "甜点", price: "$4.70", rating: "4.1", eta: "18 min"},
];
window.PAGE_SIZE = 8;

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])); }

function card(p) {
  return `<article class="card" data-id="${p.id}">
    <div class="thumb" aria-hidden="true">${esc(p.name.slice(0, 1))}</div>
    <a class="title" href="item.html?id=${p.id}">${esc(p.name)}</a>
    <div class="meta"><span class="price">${esc(p.price)}</span> · <span class="rating">★ ${esc(p.rating)}</span> · <span class="eta">${esc(p.eta)}</span></div>
    <button class="add" type="button" onclick="addToCart(${p.id})">加入购物车</button>
  </article>`;
}

function addToCart(id) {
  window.__cart = (window.__cart || []).concat([id]);
  const n = document.getElementById("cart-count");
  if (n) n.textContent = String(window.__cart.length);
}

function maybePopup() {
  const q = new URLSearchParams(location.search);
  if (q.get("popup") !== "1") return;
  const ov = document.createElement("div");
  ov.id = "overlay";
  ov.innerHTML = `<div id="dialog" role="dialog" aria-modal="true" aria-label="订阅优惠">
      <button id="close-popup" type="button" aria-label="关闭">×</button>
      <h2>订阅优惠</h2><p>输入邮箱，首单立减 5 元。</p>
      <input type="email" aria-label="邮箱" placeholder="邮箱"><button type="button">订阅</button>
    </div>`;
  document.body.appendChild(ov);
  document.getElementById("close-popup").onclick = () => ov.remove();
}
