// ── State ──────────────────────────────────────────────────────────────────────
let allItems = [];
let allBrands = [];
let activeBrand = "";
let activeSize = 0;
let sessionId = "store_" + Math.random().toString(36).slice(2);
let chatOpen = false;
let botTyping = false;

// ── Emoji map for brands ───────────────────────────────────────────────────────
const BRAND_EMOJI = {
  nike: "👟", adidas: "🏃", jordan: "🏀", "new balance": "🟡",
  puma: "🐆", asics: "🏅", reebok: "💪", vans: "🛹", converse: "⭐",
};

function brandEmoji(brand) {
  return BRAND_EMOJI[(brand || "").toLowerCase()] || "👟";
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function formatPrice(value) {
  return Number(value || 0).toLocaleString("ru-RU") + " ₸";
}

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function parseSizes(sizesStr) {
  if (!sizesStr) return [];
  return sizesStr
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .sort((a, b) => parseFloat(a) - parseFloat(b));
}

// ── Catalog rendering ──────────────────────────────────────────────────────────
function renderBrandFilters(brands) {
  const row = document.getElementById("brand-filters");
  row.innerHTML = brands.map((b) => `
    <button class="chip ${activeBrand === b ? "active" : ""}" data-brand="${escapeHtml(b)}">${escapeHtml(b)}</button>
  `).join("");
  row.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeBrand = activeBrand === btn.dataset.brand ? "" : btn.dataset.brand;
      renderAll();
    });
  });
}

function renderSizeFilters(items) {
  const sizeSet = new Set();
  items.forEach((item) => parseSizes(item.sizes).forEach((s) => sizeSet.add(parseFloat(s))));
  const sizes = [...sizeSet].sort((a, b) => a - b);

  const row = document.getElementById("size-filters");
  row.innerHTML = sizes.map((s) => `
    <button class="chip ${activeSize === s ? "active" : ""}" data-size="${s}">${s}</button>
  `).join("");
  row.querySelectorAll(".chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sz = parseFloat(btn.dataset.size);
      activeSize = activeSize === sz ? 0 : sz;
      renderAll();
    });
  });
}

function filteredItems() {
  return allItems.filter((item) => {
    if (activeBrand && item.brand !== activeBrand) return false;
    if (activeSize) {
      const sizes = parseSizes(item.sizes).map(parseFloat);
      if (!sizes.includes(activeSize)) return false;
    }
    return true;
  });
}

function renderCatalog(items) {
  const grid = document.getElementById("catalog-grid");
  const empty = document.getElementById("empty-state");

  if (!items.length) {
    grid.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  grid.innerHTML = items.map((item) => {
    const sizes = parseSizes(item.sizes);
    const sizeDots = sizes.map((s) => `<span class="size-dot">${s}</span>`).join("");
    const price = item.min_price === item.max_price
      ? formatPrice(item.min_price)
      : `от ${formatPrice(item.min_price)}`;
    const cat = escapeHtml(item.category || "");
    const colorway = escapeHtml(item.colorway || item.model || "");

    return `
      <article class="product-card">
        <div class="card-image">
          ${brandEmoji(item.brand)}
          ${cat ? `<span class="card-category">${cat}</span>` : ""}
        </div>
        <div class="card-body">
          <div class="card-brand">${escapeHtml(item.brand)}</div>
          <div class="card-model">${escapeHtml(item.model)}</div>
          ${item.colorway ? `<div class="card-colorway">${escapeHtml(item.colorway)}</div>` : ""}
          <div class="card-sizes">${sizeDots}</div>
          <div class="card-footer">
            <div class="card-price">${price}</div>
            <div class="card-qty">остаток ${item.total_qty || 0}</div>
          </div>
          <button class="card-btn" data-model="${escapeHtml(item.model)}" data-brand="${escapeHtml(item.brand)}">
            Уточнить у бота
          </button>
        </div>
      </article>
    `;
  }).join("");

  grid.querySelectorAll(".card-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const text = `Расскажи про ${btn.dataset.brand} ${btn.dataset.model}`;
      openChat();
      sendMessage(text);
    });
  });
}

function renderAll() {
  const items = filteredItems();
  const reset = document.getElementById("reset-filters");
  reset.classList.toggle("hidden", !activeBrand && !activeSize);

  renderBrandFilters(allBrands);
  renderCatalog(items);
  document.getElementById("header-count").textContent =
    items.length ? `${items.length} моделей` : "";
}

// ── API ────────────────────────────────────────────────────────────────────────
async function loadCatalog() {
  try {
    const res = await fetch("/api/catalog");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allItems = data.items || [];
    allBrands = data.brands || [];
    document.getElementById("loading").classList.add("hidden");
    renderSizeFilters(allItems);
    renderAll();
  } catch (err) {
    document.getElementById("loading").innerHTML =
      `<p style="color:#991b1b">Не удалось загрузить каталог: ${escapeHtml(err.message)}</p>`;
  }
}

// ── Chat ───────────────────────────────────────────────────────────────────────
function openChat() {
  chatOpen = true;
  document.getElementById("chat-widget").classList.remove("hidden");
  document.getElementById("chat-fab").classList.add("hidden");
  document.getElementById("chat-input").focus();
  scrollChatToBottom();
}

function closeChat() {
  chatOpen = false;
  document.getElementById("chat-widget").classList.add("hidden");
  document.getElementById("chat-fab").classList.remove("hidden");
}

function scrollChatToBottom() {
  const msgs = document.getElementById("chat-messages");
  msgs.scrollTop = msgs.scrollHeight;
}

function appendBubble(text, role) {
  const msgs = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = `chat-bubble ${role}`;
  div.textContent = text;
  msgs.appendChild(div);
  scrollChatToBottom();
  return div;
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || botTyping) return;

  appendBubble(text, "user");
  document.getElementById("chat-input").value = "";

  botTyping = true;
  const typingEl = appendBubble("Печатает…", "typing");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    typingEl.remove();
    if (!res.ok) { appendBubble("Ошибка сервера. Попробуйте ещё раз.", "bot"); return; }
    const data = await res.json();
    appendBubble(data.reply || "…", "bot");
  } catch {
    typingEl.remove();
    appendBubble("Нет связи с ботом.", "bot");
  } finally {
    botTyping = false;
  }
}

// ── Event listeners ────────────────────────────────────────────────────────────
document.getElementById("chat-toggle").addEventListener("click", openChat);
document.getElementById("chat-fab").addEventListener("click", openChat);
document.getElementById("chat-close").addEventListener("click", closeChat);
document.getElementById("ask-bot-empty").addEventListener("click", openChat);

document.getElementById("chat-send").addEventListener("click", () => {
  sendMessage(document.getElementById("chat-input").value);
});

document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage(e.target.value);
  }
});

document.getElementById("reset-filters").addEventListener("click", () => {
  activeBrand = "";
  activeSize = 0;
  renderAll();
  renderSizeFilters(allItems);
});

// ── Init ───────────────────────────────────────────────────────────────────────
loadCatalog();
