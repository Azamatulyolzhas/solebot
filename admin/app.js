const TAB_TITLES = {
  overview: ["Обзор", "Статистика магазина и активность бота"],
  products: ["Каталог", "Все товары на складе"],
  orders: ["Заказы", "Заявки от клиентов через бота"],
  import: ["Импорт CSV", "Загрузка и обновление каталога"],
  messages: ["Сообщения", "История диалогов с клиентами"],
};

const STAT_LABELS = {
  sneakers: "Товары",
  orders: "Заказы",
  conversations: "Диалоги",
  messages: "Сообщения",
  analytics_events: "События",
  total_tokens: "Токены AI",
};

let token = new URLSearchParams(window.location.search).get("token") || sessionStorage.getItem("admin_token") || "";
let productsOffset = 0;
const productsLimit = 50;

const loginScreen = document.getElementById("login-screen");
const app = document.getElementById("app");
const toast = document.getElementById("toast");

function showToast(message) {
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 3200);
}

function authQuery(extra = "") {
  const join = extra.includes("?") ? "&" : "?";
  return `${extra}${join}token=${encodeURIComponent(token)}`;
}

async function api(path, options = {}) {
  const url = authQuery(path);
  const response = await fetch(url, options);
  if (response.status === 403 || response.status === 404) {
    logout();
    throw new Error("Неверный токен");
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function logout() {
  sessionStorage.removeItem("admin_token");
  token = "";
  app.classList.add("hidden");
  loginScreen.classList.remove("hidden");
}

function enterApp() {
  sessionStorage.setItem("admin_token", token);
  const url = new URL(window.location.href);
  url.searchParams.set("token", token);
  window.history.replaceState({}, "", url);
  loginScreen.classList.add("hidden");
  app.classList.remove("hidden");
  bindLinks();
  loadAll();
}

function bindLinks() {
  document.getElementById("export-link").href = authQuery("/admin/export");
  document.getElementById("template-link").href = authQuery("/admin/import-template");
}

function switchTab(name) {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.id === `tab-${name}`);
  });
  const [title, subtitle] = TAB_TITLES[name];
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = subtitle;
}

function renderStats(data) {
  const grid = document.getElementById("stats-grid");
  const items = [
    ["sneakers", data.sneakers],
    ["orders", data.orders],
    ["conversations", data.conversations],
    ["messages", data.messages],
    ["analytics_events", data.analytics_events],
    ["total_tokens", data.total_tokens],
  ];
  grid.innerHTML = items.map(([key, value]) => `
    <article class="stat-card">
      <span>${STAT_LABELS[key]}</span>
      <strong>${value ?? 0}</strong>
    </article>
  `).join("");
  document.getElementById("db-badge").textContent = `${data.database || "db"} · shop #${data.shop_id || "?"}`;
}

function renderMessages(items, targetId) {
  const root = document.getElementById(targetId);
  if (!items.length) {
    root.innerHTML = `<div class="panel-body muted">Сообщений пока нет</div>`;
    return;
  }
  root.innerHTML = items.map((m) => `
    <article class="message-item ${m.role}">
      <div class="message-meta">
        <strong>${escapeHtml(m.channel || "—")}</strong>
        <span>${escapeHtml(m.external_user_id || "—")}</span>
        <span>${escapeHtml(m.role || "—")}</span>
        <span>${formatDate(m.created_at)}</span>
      </div>
      <p>${escapeHtml(m.content || "")}</p>
    </article>
  `).join("");
}

function renderProducts(data) {
  document.getElementById("products-count").textContent = `${data.count} позиций`;
  document.getElementById("products-page-info").textContent =
    `${productsOffset + 1}–${Math.min(productsOffset + data.items.length, data.count)} из ${data.count}`;
  document.getElementById("products-prev").disabled = productsOffset <= 0;
  document.getElementById("products-next").disabled = productsOffset + productsLimit >= data.count;

  const body = document.getElementById("products-body");
  body.innerHTML = data.items.map((p) => `
    <tr>
      <td>${escapeHtml(p.brand)}</td>
      <td>${escapeHtml(p.model)}</td>
      <td>${escapeHtml(p.colorway || "—")}</td>
      <td>${escapeHtml(p.size)}</td>
      <td>${escapeHtml(p.quantity)}</td>
      <td>${formatPrice(p.price)}</td>
      <td>${escapeHtml(p.category || "—")}</td>
    </tr>
  `).join("") || `<tr><td colspan="7" class="muted">Каталог пуст</td></tr>`;
}

function renderOrders(data) {
  document.getElementById("orders-count").textContent = `${data.count} заказов`;
  const body = document.getElementById("orders-body");
  body.innerHTML = data.items.map((o) => `
    <tr>
      <td>#${escapeHtml(o.id)}</td>
      <td>${escapeHtml(o.channel || "—")}</td>
      <td>${escapeHtml(o.external_user_id || "—")}</td>
      <td>${escapeHtml(o.customer_name || "—")}</td>
      <td>${escapeHtml(o.customer_phone || "—")}</td>
      <td>${escapeHtml(o.product_interest || "—")}</td>
      <td><span class="status-pill">${escapeHtml(o.status || "new")}</span></td>
      <td>${formatDate(o.created_at)}</td>
    </tr>
  `).join("") || `<tr><td colspan="8" class="muted">Заказов пока нет</td></tr>`;
}

function renderImportPreview(result) {
  const root = document.getElementById("import-result");
  if (!result.valid) {
    root.innerHTML = `
      <p><strong>Ошибки в CSV (${result.error_count})</strong></p>
      <ul class="error-list">${result.errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("")}</ul>
    `;
    return;
  }

  const rows = (result.preview || []).map((p) => `
    <tr>
      <td>${escapeHtml(p.brand)}</td>
      <td>${escapeHtml(p.model)}</td>
      <td>${escapeHtml(p.size)}</td>
      <td>${escapeHtml(p.quantity)}</td>
      <td>${formatPrice(p.price)}</td>
    </tr>
  `).join("");

  root.innerHTML = `
    <p><strong>Файл валиден:</strong> ${result.valid_rows} строк</p>
    <table class="preview-table">
      <thead><tr><th>Бренд</th><th>Модель</th><th>Размер</th><th>Qty</th><th>Цена</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function loadStats() {
  const data = await api("/admin/stats");
  renderStats(data);
}

async function loadMessages(limit = 30, targetId = "messages-list") {
  const data = await api(`/admin/messages?limit=${limit}`);
  renderMessages(data.items, targetId);
}

async function loadProducts() {
  const data = await api(`/admin/products?limit=${productsLimit}&offset=${productsOffset}`);
  renderProducts(data);
}

async function loadOrders() {
  const data = await api("/admin/orders?limit=100");
  renderOrders(data);
}

async function loadAll() {
  try {
    await Promise.all([
      loadStats(),
      loadMessages(8, "overview-messages"),
      loadMessages(40, "messages-list"),
      loadProducts(),
      loadOrders(),
    ]);
  } catch (error) {
    showToast(error.message || "Ошибка загрузки");
  }
}

async function uploadCsv(path) {
  const fileInput = document.getElementById("csv-file");
  const file = fileInput.files?.[0];
  if (!file) {
    showToast("Выберите CSV файл");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(authQuery(path), { method: "POST", body: form });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatPrice(value) {
  return `${Number(value || 0).toLocaleString("ru-RU")} ₸`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ru-RU");
}

document.getElementById("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  token = document.getElementById("token-input").value.trim();
  if (!token) return;
  enterApp();
});

document.getElementById("logout-btn").addEventListener("click", logout);

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

document.getElementById("refresh-btn").addEventListener("click", loadAll);

document.getElementById("products-prev").addEventListener("click", async () => {
  productsOffset = Math.max(0, productsOffset - productsLimit);
  await loadProducts();
});

document.getElementById("products-next").addEventListener("click", async () => {
  productsOffset += productsLimit;
  await loadProducts();
});

document.getElementById("preview-btn").addEventListener("click", async () => {
  try {
    const result = await uploadCsv("/admin/import-preview");
    renderImportPreview(result);
  } catch (error) {
    showToast(error.message || "Ошибка предпросмотра");
  }
});

document.getElementById("import-btn").addEventListener("click", async () => {
  try {
    const result = await uploadCsv("/admin/import");
    showToast(`Импортировано: ${result.imported}`);
    await loadAll();
  } catch (error) {
    showToast(error.message || "Ошибка импорта");
  }
});

document.getElementById("replace-btn").addEventListener("click", async () => {
  if (!window.confirm("Заменить весь каталог? Старые позиции будут удалены.")) return;
  try {
    const result = await uploadCsv("/admin/import?replace=true");
    showToast(`Каталог заменён: ${result.imported} позиций`);
    await loadAll();
  } catch (error) {
    showToast(error.message || "Ошибка замены");
  }
});

if (token) {
  enterApp();
} else {
  loginScreen.classList.remove("hidden");
}
