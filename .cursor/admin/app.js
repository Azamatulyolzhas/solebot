const TAB_TITLES = {
  overview:     ["Обзор",      "Статистика магазина и активность бота"],
  products:     ["Каталог",    "Все товары на складе"],
  orders:       ["Заказы",     "Заявки от клиентов через бота"],
  import:       ["Импорт CSV", "Загрузка и обновление каталога"],
  messages:     ["Сообщения",  "История диалогов с клиентами"],
  applications: ["Заявки",     "Новые магазины ожидают активации"],
  shops:        ["Магазины",   "Все зарегистрированные магазины"],
};

const STAT_LABELS = {
  products: "Товары",
  orders: "Заказы",
  conversations: "Диалоги",
  messages: "Сообщения",
  analytics_events: "События",
  total_tokens: "Токены AI",
};

const ORDER_STATUSES = ["new", "confirmed", "done", "cancelled"];
const STATUS_LABELS = { new: "Новый", confirmed: "Подтверждён", done: "Выполнен", cancelled: "Отменён" };
const STATUS_CLASS = { new: "status-new", confirmed: "status-confirmed", done: "status-done", cancelled: "status-cancelled" };

let token = sessionStorage.getItem("admin_token") || "";
let productsOffset = 0;
const productsLimit = 50;
let allProducts = [];
let allShops    = [];

const loginScreen = document.getElementById("login-screen");
const app = document.getElementById("app");
const toast = document.getElementById("toast");
let toastTimer = null;

function showToast(message, type = "info") {
  toast.textContent = message;
  toast.className = `toast toast-${type}`;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 3400);
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

function authQuery(extra = "") {
  const join = extra.includes("?") ? "&" : "?";
  return `${extra}${join}token=${encodeURIComponent(token)}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
  if (response.status === 403 || response.status === 401) {
    logout();
    throw new Error("Сессия истекла");
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response.text();
}

async function patchJson(path, body) {
  return api(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function logout() {
  sessionStorage.removeItem("admin_token");
  token = "";
  app.classList.add("hidden");
  loginScreen.classList.remove("hidden");
}

async function enterApp() {
  sessionStorage.setItem("admin_token", token);
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url);
  loginScreen.classList.add("hidden");
  app.classList.remove("hidden");
  bindLinks();
  await loadAll();
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

// ── Stats ──────────────────────────────────────────────────────────────────────

function renderStats(data) {
  const grid = document.getElementById("stats-grid");
  const items = [
    ["products", data.products],
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

// ── Messages ───────────────────────────────────────────────────────────────────

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
        <span class="role-badge role-${m.role}">${escapeHtml(m.role || "—")}</span>
        <span>${formatDate(m.created_at)}</span>
      </div>
      <p>${escapeHtml(m.content || "")}</p>
    </article>
  `).join("");
}

// ── Products ───────────────────────────────────────────────────────────────────

function renderProducts(data) {
  allProducts = data.items;
  document.getElementById("products-count").textContent = `${data.count} позиций`;
  document.getElementById("products-page-info").textContent =
    `${productsOffset + 1}–${Math.min(productsOffset + data.items.length, data.count)} из ${data.count}`;
  document.getElementById("products-prev").disabled = productsOffset <= 0;
  document.getElementById("products-next").disabled = productsOffset + productsLimit >= data.count;

  const q = document.getElementById("products-search").value.trim().toLowerCase();
  renderProductsFiltered(q ? allProducts.filter((p) => matchProduct(p, q)) : allProducts);
}

function matchProduct(p, q) {
  return (
    (p.brand || "").toLowerCase().includes(q) ||
    (p.model || "").toLowerCase().includes(q) ||
    (p.colorway || "").toLowerCase().includes(q) ||
    (p.category || "").toLowerCase().includes(q)
  );
}

function renderProductsFiltered(items) {
  const body = document.getElementById("products-body");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="7" class="muted center">Ничего не найдено</td></tr>`;
    return;
  }
  body.innerHTML = items.map((p) => `
    <tr data-id="${p.id}">
      <td>${escapeHtml(p.brand)}</td>
      <td>${escapeHtml(p.model)}</td>
      <td>${escapeHtml(p.colorway || "—")}</td>
      <td>${escapeHtml(p.size)}</td>
      <td class="editable-cell" data-field="quantity" data-value="${p.quantity}">${escapeHtml(p.quantity)}</td>
      <td class="editable-cell" data-field="price" data-value="${p.price}">${formatPrice(p.price)}</td>
      <td>${escapeHtml(p.category || "—")}</td>
    </tr>
  `).join("");
  attachEditListeners();
}

function attachEditListeners() {
  document.querySelectorAll("#products-body .editable-cell").forEach((cell) => {
    cell.addEventListener("click", startEdit);
  });
}

function startEdit(e) {
  const cell = e.currentTarget;
  if (cell.querySelector("input")) return; // already editing
  const original = cell.dataset.value;
  const field = cell.dataset.field;
  const input = document.createElement("input");
  input.type = "number";
  input.min = field === "price" ? "1" : "0";
  input.value = original;
  input.className = "inline-input";
  cell.textContent = "";
  cell.appendChild(input);
  input.focus();
  input.select();

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); commitEdit(cell, input, field, original); }
    if (ev.key === "Escape") { ev.preventDefault(); cancelEdit(cell, field, original); }
  });
  input.addEventListener("blur", () => commitEdit(cell, input, field, original));
}

async function commitEdit(cell, input, field, original) {
  const newVal = parseInt(input.value, 10);
  if (isNaN(newVal) || newVal === parseInt(original, 10)) {
    cancelEdit(cell, field, original);
    return;
  }
  const row = cell.closest("tr");
  const id = row.dataset.id;
  cell.innerHTML = `<span class="saving">…</span>`;
  try {
    await patchJson(`/admin/products/${id}`, { [field]: newVal });
    cell.dataset.value = newVal;
    cell.textContent = field === "price" ? formatPrice(newVal) : String(newVal);
    cell.addEventListener("click", startEdit);
    showToast(`Сохранено: ${field === "price" ? "цена" : "количество"} → ${newVal}`, "success");
  } catch (err) {
    cell.textContent = field === "price" ? formatPrice(original) : String(original);
    cell.addEventListener("click", startEdit);
    showToast(err.message || "Ошибка сохранения", "error");
  }
}

function cancelEdit(cell, field, original) {
  cell.textContent = field === "price" ? formatPrice(original) : String(original);
  cell.addEventListener("click", startEdit);
}

// ── Orders ─────────────────────────────────────────────────────────────────────

function renderOrders(data) {
  document.getElementById("orders-count").textContent = `${data.count} заказов`;
  const body = document.getElementById("orders-body");
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="8" class="muted center">Заказов пока нет</td></tr>`;
    return;
  }
  body.innerHTML = data.items.map((o) => `
    <tr>
      <td>#${escapeHtml(o.id)}</td>
      <td>${escapeHtml(o.channel || "—")}</td>
      <td>${escapeHtml(o.external_user_id || "—")}</td>
      <td>${escapeHtml(o.customer_name || "—")}</td>
      <td>${escapeHtml(o.customer_phone || "—")}</td>
      <td class="interest-cell">${escapeHtml(o.product_interest || "—")}</td>
      <td>
        <select class="status-select ${STATUS_CLASS[o.status] || "status-new"}" data-id="${o.id}" data-current="${o.status}">
          ${ORDER_STATUSES.map((s) => `<option value="${s}" ${s === o.status ? "selected" : ""}>${STATUS_LABELS[s] || s}</option>`).join("")}
        </select>
      </td>
      <td>${formatDate(o.created_at)}</td>
    </tr>
  `).join("");
  attachOrderListeners();
}

function attachOrderListeners() {
  document.querySelectorAll(".status-select").forEach((sel) => {
    sel.addEventListener("change", handleStatusChange);
  });
}

async function handleStatusChange(e) {
  const sel = e.currentTarget;
  const id = sel.dataset.id;
  const prev = sel.dataset.current;
  const newStatus = sel.value;
  sel.disabled = true;
  try {
    await patchJson(`/admin/orders/${id}`, { status: newStatus });
    sel.dataset.current = newStatus;
    sel.className = `status-select ${STATUS_CLASS[newStatus] || "status-new"}`;
    showToast(`Заказ #${id}: ${STATUS_LABELS[newStatus]}`, "success");
  } catch (err) {
    sel.value = prev;
    showToast(err.message || "Ошибка", "error");
  } finally {
    sel.disabled = false;
  }
}

// ── Import ─────────────────────────────────────────────────────────────────────

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

// ── Data loading ───────────────────────────────────────────────────────────────

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

// ── Applications ───────────────────────────────────────────────────────────────
async function loadApplications() {
  const data = await api("/admin/applications");
  const items = data.items || [];
  const badge = document.getElementById("apps-badge");
  const count = document.getElementById("apps-count");
  count.textContent = `${items.length} заявок`;

  if (items.length > 0) {
    badge.textContent = items.length;
    badge.classList.remove("hidden");
  } else {
    badge.classList.add("hidden");
  }

  const body = document.getElementById("applications-body");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="5" class="muted center">Новых заявок нет</td></tr>`;
    return;
  }
  body.innerHTML = items.map((s) => `
    <tr data-id="${s.id}">
      <td>#${escapeHtml(s.id)}</td>
      <td><strong>${escapeHtml(s.name)}</strong></td>
      <td>${escapeHtml(s.owner_email || "—")}</td>
      <td>${formatDate(s.created_at)}</td>
      <td>
        <div style="display:flex;gap:8px">
          <button class="btn approve-btn" data-id="${s.id}">Одобрить</button>
          <button class="btn secondary reject-btn" data-id="${s.id}">Отклонить</button>
        </div>
      </td>
    </tr>
  `).join("");

  body.querySelectorAll(".approve-btn").forEach(btn => btn.addEventListener("click", () => updateShopStatus(btn.dataset.id, "active")));
  body.querySelectorAll(".reject-btn").forEach(btn => btn.addEventListener("click", () => updateShopStatus(btn.dataset.id, "rejected")));
}

async function updateShopStatus(shopId, status) {
  const label = status === "active" ? "Одобрить" : "Отклонить";
  if (!confirm(`${label} магазин #${shopId}?`)) return;
  try {
    await api(`/admin/shops/${shopId}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    showToast(status === "active" ? `Магазин #${shopId} активирован` : `Магазин #${shopId} отклонён`, "success");
    await loadApplications();
  } catch (err) {
    showToast(err.message || "Ошибка", "error");
  }
}

// ── Shops ──────────────────────────────────────────────────────────────────────

const SHOP_STATUS_LABELS = {
  active: "Активен", pending: "На модерации", suspended: "Заблокирован",
  rejected: "Отклонён", deleted: "Удалён",
};
const SHOP_STATUS_CLASS  = {
  active: "status-done", pending: "status-new", suspended: "status-cancelled",
  rejected: "status-cancelled", deleted: "status-cancelled",
};
const SHOP_STATUSES      = ["active", "pending", "suspended", "rejected", "deleted"];
let includeDeletedShops  = false;

function renderShopsFiltered(items) {
  const body = document.getElementById("shops-body");
  if (!items.length) {
    body.innerHTML = `<tr><td colspan="9" class="muted center">Магазинов не найдено</td></tr>`;
    return;
  }
  body.innerHTML = items.map((s) => {
    const ends = s.period_ends_at || s.trial_ends_at;
    const endsStr = ends ? new Date(ends).toLocaleDateString("ru-RU") : "—";
    const isExpired = ends && new Date(ends) < new Date();
    const subBadge = isExpired
      ? `<span class="status-badge badge-error">Истекла</span>`
      : ends
        ? `<span class="status-badge badge-active">${escapeHtml(s.plan || "trial")} до ${endsStr}</span>`
        : `<span class="status-badge badge-pending">нет</span>`;
    return `
      <tr data-shop-id="${s.id}">
        <td>#${escapeHtml(String(s.id))}</td>
        <td><strong>${escapeHtml(s.name)}</strong><br><small class="muted">${escapeHtml(s.owner_email || "—")}</small></td>
        <td class="mono">${escapeHtml(s.slug || "—")}</td>
        <td>${subBadge}</td>
        <td>
          <select class="status-select shop-status-select ${SHOP_STATUS_CLASS[s.status] || "status-new"}"
                  data-id="${s.id}" data-current="${s.status}">
            ${SHOP_STATUSES.map((st) => `<option value="${st}" ${st === s.status ? "selected" : ""}>${SHOP_STATUS_LABELS[st] || st}</option>`).join("")}
          </select>
        </td>
        <td>${formatDate(s.created_at)}</td>
        <td>
          <div style="display:flex;flex-wrap:wrap;gap:6px">
            <button class="btn-sm sub-extend-btn" data-id="${s.id}" data-name="${escapeHtml(s.name)}">
              💳 Подписка
            </button>
            ${s.status !== "deleted" ? `
              <button class="btn-sm secondary soft-delete-btn" data-id="${s.id}" data-name="${escapeHtml(s.name)}">
                Удалить
              </button>
              <button class="btn-sm danger hard-delete-btn" data-id="${s.id}" data-name="${escapeHtml(s.name)}" data-slug="${escapeHtml(s.slug || "")}">
                Навсегда
              </button>
            ` : ""}
          </div>
        </td>
      </tr>
    `;
  }).join("");

  body.querySelectorAll(".shop-status-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const id    = sel.dataset.id;
      const prev  = sel.dataset.current;
      const newSt = sel.value;
      sel.disabled = true;
      try {
        await api(`/admin/shops/${id}/status`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: newSt }),
        });
        sel.dataset.current = newSt;
        sel.className = `status-select shop-status-select ${SHOP_STATUS_CLASS[newSt] || "status-new"}`;
        showToast(`Магазин #${id}: ${SHOP_STATUS_LABELS[newSt]}`, "success");
      } catch (err) {
        sel.value = prev;
        showToast(err.message || "Ошибка", "error");
      } finally {
        sel.disabled = false;
      }
    });
  });

  body.querySelectorAll(".sub-extend-btn").forEach((btn) => {
    btn.addEventListener("click", () => openSubModal(btn.dataset.id, btn.dataset.name));
  });

  body.querySelectorAll(".soft-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => softDeleteShop(btn.dataset.id, btn.dataset.name));
  });

  body.querySelectorAll(".hard-delete-btn").forEach((btn) => {
    btn.addEventListener("click", () => openDeleteModal(btn.dataset.id, btn.dataset.name, btn.dataset.slug));
  });
}

async function softDeleteShop(shopId, shopName) {
  if (!confirm(`Удалить магазин «${shopName}» (#${shopId})?\n\nМягкое удаление: данные сохранятся, бот отключится.`)) return;
  try {
    await api(`/admin/shops/${shopId}?hard=false`, { method: "DELETE" });
    showToast(`Магазин #${shopId} удалён`, "success");
    await loadShops();
  } catch (err) {
    showToast(err.message || "Ошибка", "error");
  }
}

function openDeleteModal(shopId, shopName, slug) {
  document.getElementById("delete-modal-title").textContent = `Удалить навсегда: ${shopName}`;
  document.getElementById("delete-modal-shop-id").value = shopId;
  document.getElementById("delete-modal-slug").textContent = slug || "—";
  document.getElementById("delete-modal-confirm").value = "";
  document.getElementById("delete-modal").classList.remove("hidden");
}

// ── Subscription modal ────────────────────────────────────────────────────────
function openSubModal(shopId, shopName) {
  document.getElementById("sub-modal-title").textContent = `Подписка: ${shopName}`;
  document.getElementById("sub-modal-shop-id").value = shopId;
  document.getElementById("sub-modal-plan").value = "basic";
  document.getElementById("sub-modal-days").value = "30";
  document.getElementById("sub-modal").classList.remove("hidden");
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("delete-modal-close").addEventListener("click", () => {
    document.getElementById("delete-modal").classList.add("hidden");
  });

  document.getElementById("delete-modal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const shopId = document.getElementById("delete-modal-shop-id").value;
    const slug   = document.getElementById("delete-modal-confirm").value.trim();
    const btn    = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/admin/shops/${shopId}?hard=true&confirm_slug=${encodeURIComponent(slug)}`, { method: "DELETE" });
      showToast(`Магазин #${shopId} удалён навсегда`, "success");
      document.getElementById("delete-modal").classList.add("hidden");
      await loadShops();
    } catch (err) {
      showToast(err.message || "Ошибка", "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("sub-modal-close").addEventListener("click", () => {
    document.getElementById("sub-modal").classList.add("hidden");
  });

  document.getElementById("sub-modal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const shopId = document.getElementById("sub-modal-shop-id").value;
    const plan   = document.getElementById("sub-modal-plan").value;
    const days   = parseInt(document.getElementById("sub-modal-days").value);
    const btn    = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api(`/admin/shops/${shopId}/subscription`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan, days }),
      });
      showToast(`Подписка активирована на ${days} дней`, "success");
      document.getElementById("sub-modal").classList.add("hidden");
      await loadShops();
    } catch (err) {
      showToast(err.message || "Ошибка", "error");
    } finally {
      btn.disabled = false;
    }
  });
});

async function loadShops() {
  const deletedQ = includeDeletedShops ? "?include_deleted=true" : "";
  const data = await api(`/admin/shops${deletedQ}`);
  allShops = data.shops || [];
  document.getElementById("shops-count").textContent = `${allShops.length} магазинов`;
  const searchQ = document.getElementById("shops-search").value.trim().toLowerCase();
  renderShopsFiltered(searchQ ? allShops.filter((s) => matchShop(s, searchQ)) : allShops);
}

function matchShop(s, q) {
  return (
    (s.name || "").toLowerCase().includes(q) ||
    (s.owner_email || "").toLowerCase().includes(q) ||
    (s.slug || "").toLowerCase().includes(q)
  );
}

async function loadAll() {
  const tasks = [
    loadStats(),
    loadMessages(8, "overview-messages"),
    loadMessages(40, "messages-list"),
    loadProducts(),
    loadOrders(),
    loadApplications(),
    loadShops(),
  ];
  const results = await Promise.allSettled(tasks);
  const failed  = results.filter((r) => r.status === "rejected");
  if (failed.length) {
    const msg = failed[0].reason?.message || "Ошибка загрузки";
    showToast(msg, "error");
  }
}

async function uploadCsv(path) {
  const fileInput = document.getElementById("csv-file");
  const file = fileInput.files?.[0];
  if (!file) { showToast("Выберите CSV файл", "error"); return; }
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(path, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  if (response.status === 401 || response.status === 403) {
    logout();
    throw new Error("Сессия истекла");
  }
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

// ── Helpers ────────────────────────────────────────────────────────────────────

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

// ── Event bindings ─────────────────────────────────────────────────────────────

document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const email = document.getElementById("email-input").value.trim();
  const password = document.getElementById("password-input").value;
  const errEl = document.getElementById("login-error");
  errEl.classList.add("hidden");

  try {
    const res = await fetch("/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    let data = {};
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Ошибка входа (${res.status})`);
    token = data.token;
    await enterApp();
  } catch (error) {
    errEl.textContent = error.message || "Ошибка входа";
    errEl.classList.remove("hidden");
  }
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

document.getElementById("products-search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  renderProductsFiltered(q ? allProducts.filter((p) => matchProduct(p, q)) : allProducts);
});

document.getElementById("shops-search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  renderShopsFiltered(q ? allShops.filter((s) => matchShop(s, q)) : allShops);
});

document.getElementById("shops-include-deleted").addEventListener("change", async (e) => {
  includeDeletedShops = e.target.checked;
  await loadShops();
});

document.getElementById("preview-btn").addEventListener("click", async () => {
  try {
    const result = await uploadCsv("/admin/import-preview");
    renderImportPreview(result);
  } catch (error) {
    showToast(error.message || "Ошибка предпросмотра", "error");
  }
});

document.getElementById("import-btn").addEventListener("click", async () => {
  try {
    const result = await uploadCsv("/admin/import");
    showToast(`Импортировано: ${result.imported}`, "success");
    await loadAll();
  } catch (error) {
    showToast(error.message || "Ошибка импорта", "error");
  }
});

document.getElementById("replace-btn").addEventListener("click", async () => {
  if (!window.confirm("Заменить весь каталог? Старые позиции будут удалены.")) return;
  try {
    const result = await uploadCsv("/admin/import?replace=true");
    showToast(`Каталог заменён: ${result.imported} позиций`, "success");
    await loadAll();
  } catch (error) {
    showToast(error.message || "Ошибка замены", "error");
  }
});

if (token) {
  enterApp().catch(() => {
    sessionStorage.removeItem("admin_token");
    token = "";
    loginScreen.classList.remove("hidden");
  });
} else {
  loginScreen.classList.remove("hidden");
}
