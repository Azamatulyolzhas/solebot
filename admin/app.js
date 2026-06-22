const TAB_TITLES = {
  overview:     ["Обзор",      "Аналитика платформы и магазинов"],
  applications: ["Заявки",     "Новые магазины ожидают активации"],
  shops:        ["Магазины",   "Все зарегистрированные магазины"],
  funnel:       ["Воронка",    "Прохождение этапов активации"],
  email:        ["Email",      "Домен Resend — письма на любые адреса клиентов"],
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
let statsData   = null;
let appsCount   = 0;

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

async function downloadWithAuth(path, fallbackName) {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) {
    showToast(`Не удалось скачать: HTTP ${res.status}`, "error");
    return;
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const name = match ? match[1] : fallbackName;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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
  const exp = document.getElementById("export-link");
  if (exp) {
    exp.href = "#";
    exp.addEventListener("click", (e) => {
      e.preventDefault();
      downloadWithAuth("/admin/export", "solebot-products.csv");
    });
  }
  const tpl = document.getElementById("template-link");
  if (tpl) {
    tpl.href = "#";
    tpl.addEventListener("click", (e) => {
      e.preventDefault();
      downloadWithAuth("/admin/import-template", "template.csv");
    });
  }
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

const ICON = {
  store:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l1-5h16l1 5"/><path d="M4 9v11a1 1 0 001 1h14a1 1 0 001-1V9"/><path d="M9 21V13h6v8"/></svg>`,
  check:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
  file:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
  card:     `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>`,
  activity: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
  zap:      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
};

// loadStats only stores the data; the overview is composed in renderOverview()
function renderStats(data) {
  statsData = data;
  document.getElementById("db-badge").textContent = `${data.database || "db"} · shop #${data.shop_id || "?"}`;
}

function statCard(label, value, color, svg) {
  return `
    <article class="stat-card">
      <div class="stat-card-text">
        <span class="stat-card-label">${label}</span>
        <span class="stat-card-num">${Number(value ?? 0).toLocaleString("ru-RU")}</span>
      </div>
      <span class="stat-icon-badge" style="background:color-mix(in srgb, ${color} 14%, transparent);color:${color}">${svg}</span>
    </article>`;
}

function barRow(label, count, max, color) {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0;
  return `
    <div class="a-bar-row">
      <span class="a-bar-label">${escapeHtml(label)}</span>
      <div class="a-bar-track"><div class="a-bar-fill" style="width:${pct}%;background:${color}"></div></div>
      <span class="a-bar-pct">${count}</span>
    </div>`;
}

// ── Overview analytics (composed from shops + applications + stats) ──────────────
function renderOverview() {
  const shops    = (allShops || []).filter((s) => s.status !== "deleted");
  const now      = new Date();
  const total    = shops.length;
  const active   = shops.filter((s) => s.status === "active").length;
  const paid     = shops.filter((s) => {
    const ends = s.period_ends_at || s.trial_ends_at;
    return s.plan && s.plan !== "trial" && (!ends || new Date(ends) >= now);
  }).length;
  const tokens   = statsData?.total_tokens ?? 0;
  const events   = statsData?.analytics_events ?? 0;

  const grid = document.getElementById("stats-grid");
  if (grid) {
    grid.innerHTML = [
      statCard("Магазины",       total,     "#3B82F6", ICON.store),
      statCard("Активные",       active,    "#16a34a", ICON.check),
      statCard("Заявки",         appsCount, "#ea580c", ICON.file),
      statCard("Платные тарифы", paid,      "#8b5cf6", ICON.card),
      statCard("События AI",     events,    "#0ea5e9", ICON.activity),
      statCard("Токены AI",      tokens,    "#e11d48", ICON.zap),
    ].join("");
  }

  // Plans breakdown
  const plansEl = document.getElementById("plans-bars");
  if (plansEl) {
    const planDefs = [
      ["trial", "Trial",  "#ea580c"],
      ["basic", "Basic",  "#3B82F6"],
      ["pro",   "Pro",    "#16a34a"],
    ];
    const counts = planDefs.map(([key]) => shops.filter((s) => (s.plan || "trial") === key).length);
    const noPlan = shops.filter((s) => !s.plan).length;
    const maxP   = Math.max(1, ...counts, noPlan);
    plansEl.innerHTML = total
      ? planDefs.map(([, lbl, color], i) => barRow(lbl, counts[i], maxP, color)).join("") +
        (noPlan ? barRow("Без тарифа", noPlan, maxP, "#7c7c80") : "")
      : `<p class="muted center" style="padding:8px 0">Нет данных</p>`;
  }

  // Status breakdown
  const statusEl = document.getElementById("status-bars");
  if (statusEl) {
    const statusDefs = [
      ["active",    "Активны",     "#16a34a"],
      ["pending",   "На модерации","#ea580c"],
      ["suspended", "Заблокированы","#e11d48"],
      ["rejected",  "Отклонены",   "#7c7c80"],
    ];
    const sCounts = statusDefs.map(([key]) => shops.filter((s) => s.status === key).length);
    const maxS    = Math.max(1, ...sCounts);
    statusEl.innerHTML = total
      ? statusDefs.map(([, lbl, color], i) => barRow(lbl, sCounts[i], maxS, color)).join("")
      : `<p class="muted center" style="padding:8px 0">Нет данных</p>`;
  }

  // Recent shops
  const recentBody = document.getElementById("recent-shops-body");
  if (recentBody) {
    const recent = [...shops]
      .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
      .slice(0, 6);
    const meta = document.getElementById("recent-shops-meta");
    if (meta) meta.textContent = `${total} всего`;
    recentBody.innerHTML = recent.length
      ? recent.map((s) => {
          const cls = SHOP_STATUS_CLASS[s.status] || "status-new";
          const lbl = SHOP_STATUS_LABELS[s.status] || s.status || "—";
          return `
            <tr>
              <td><strong>${escapeHtml(s.name || "—")}</strong></td>
              <td>${escapeHtml(s.owner_email || "—")}</td>
              <td>${escapeHtml(s.plan || "trial")}</td>
              <td><span class="status-select ${cls}" style="cursor:default">${escapeHtml(lbl)}</span></td>
              <td>${formatDate(s.created_at)}</td>
            </tr>`;
        }).join("")
      : `<tr><td colspan="5" class="muted center">Магазинов пока нет</td></tr>`;
  }
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

// ── Applications ───────────────────────────────────────────────────────────────
async function loadApplications() {
  const data = await api("/admin/applications");
  const items = data.items || [];
  appsCount = items.length;
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
              Подписка
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
    loadShops(),
    loadFunnel(),
  ];
  const results = await Promise.allSettled(tasks);
  const failed  = results.filter((r) => r.status === "rejected");
  if (failed.length) {
    const msg = failed[0].reason?.message || "Ошибка загрузки";
    showToast(msg, "error");
  }
  renderOverview();
}

document.getElementById("backup-download-btn")?.addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Готовлю архив…";
  try {
    await downloadWithAuth("/admin/backup", "vendly-backup.zip");
    showToast("Бэкап скачан", "success");
  } catch (err) {
    showToast(err.message || "Ошибка", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

async function loadFunnel() {
  const target = document.getElementById("funnel-content");
  if (!target) return;
  try {
    const data = await api("/admin/funnel");
    renderFunnel(data);
  } catch (err) {
    target.innerHTML = `<p class="muted">${escapeHtml(err.message || "Ошибка")}</p>`;
  }
}

function renderFunnel(data) {
  const baseline = document.getElementById("funnel-baseline");
  if (baseline) baseline.textContent = `всего магазинов: ${data.totals?.shops ?? 0}`;
  const stages = data.stages || [];
  const max = Math.max(1, ...stages.map(s => s.baseline || s.count || 0));
  const rows = stages.map((s, i) => {
    const cnt = s.count || 0;
    const widthPct = Math.round(cnt / max * 100);
    const conv = i === 0 ? "" : (() => {
      const prev = stages[i - 1].count || 0;
      const p = prev > 0 ? Math.round(cnt / prev * 100) : 0;
      return `<span class="muted small" style="margin-left:8px">→ ${p}% от пред.</span>`;
    })();
    return `<div style="margin-bottom:14px">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px">
        <span><strong>${escapeHtml(s.label)}</strong>${conv}</span>
        <span class="mono">${cnt} / ${s.baseline || 0}</span>
      </div>
      <div style="height:10px;background:var(--line2);border-radius:999px;overflow:hidden">
        <div style="height:100%;width:${widthPct}%;background:var(--accent);border-radius:999px"></div>
      </div>
    </div>`;
  }).join("");
  const c = data.conversion_pct || {};
  const summary = `<div class="muted small" style="margin-top:18px;padding-top:14px;border-top:1px solid var(--line)">
    Итог: <strong>${c.lead_from_signup ?? 0}%</strong> signup → first_lead.
  </div>`;
  document.getElementById("funnel-content").innerHTML = rows + summary;
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

// ── Email (Resend) ─────────────────────────────────────────────────────────────

function renderEmailStatus(data) {
  const badge = document.getElementById("email-status-badge");
  const hint = document.getElementById("email-hint");
  const list = document.getElementById("email-domains-list");
  if (!badge || !hint || !list) return;

  if (!data.configured) {
    badge.textContent = "Не настроен";
    badge.className = "status-badge badge-pending";
    hint.textContent = "Добавьте RESEND_API_KEY в Railway.";
    list.innerHTML = "";
    return;
  }

  if (data.production_ready) {
    badge.textContent = "Production";
    badge.className = "status-badge badge-active";
  } else {
    badge.textContent = "Sandbox";
    badge.className = "status-badge badge-pending";
  }

  hint.textContent = `${data.hint} Отправитель: ${data.from_address}`;

  const domains = data.domains || [];
  if (!domains.length) {
    list.innerHTML = `<p class="muted small">Доменов нет — добавьте ниже.</p>`;
    return;
  }

  list.innerHTML = `
    <table class="compact-table"><thead><tr><th>Домен</th><th>Статус</th><th></th></tr></thead>
    <tbody>${domains.map((d) => `
      <tr>
        <td>${escapeHtml(d.name)}</td>
        <td>${escapeHtml(d.status)}</td>
        <td><button type="button" class="btn secondary small email-verify-btn" data-id="${escapeHtml(d.id)}">Проверить DNS</button></td>
      </tr>`).join("")}
    </tbody></table>`;

  list.querySelectorAll(".email-verify-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await api(`/admin/email/verify/${btn.dataset.id}`, { method: "POST" });
        showToast("Проверка DNS запущена", "success");
        await loadEmail();
      } catch (err) {
        showToast(err.message, "error");
      }
    });
  });
}

function renderDnsRecords(records) {
  const box = document.getElementById("email-dns-records");
  if (!box) return;
  if (!records?.length) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `
    <h3>DNS-записи (добавьте у регистратора домена)</h3>
    <table class="compact-table"><thead><tr><th>Тип</th><th>Имя</th><th>Значение</th></tr></thead>
    <tbody>${records.map((r) => `
      <tr>
        <td>${escapeHtml(r.type)}</td>
        <td><code>${escapeHtml(r.name)}</code></td>
        <td><code>${escapeHtml(r.value)}</code></td>
      </tr>`).join("")}
    </tbody></table>`;
}

async function loadEmail() {
  const data = await api("/admin/email");
  renderEmailStatus(data);
}

document.getElementById("email-add-domain-btn")?.addEventListener("click", async () => {
  const domain = document.getElementById("email-domain-input")?.value.trim();
  const msg = document.getElementById("email-action-msg");
  if (!domain) {
    showToast("Введите домен", "error");
    return;
  }
  try {
    const res = await api("/admin/email/domain", { method: "POST", json: { domain } });
    renderDnsRecords(res.records);
    if (msg) msg.textContent = `Домен ${res.domain?.name} добавлен. Пропишите DNS и нажмите «Проверить DNS».`;
    showToast("Домен добавлен — настройте DNS", "success");
    await loadEmail();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("email-test-btn")?.addEventListener("click", async () => {
  const to = document.getElementById("email-test-input")?.value.trim();
  if (!to) {
    showToast("Введите email для теста", "error");
    return;
  }
  try {
    const res = await api("/admin/email/test", { method: "POST", json: { to } });
    showToast(`Тест отправлен на ${res.to}`, "success");
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    switchTab(btn.dataset.tab);
    if (btn.dataset.tab === "email") loadEmail().catch((e) => showToast(e.message, "error"));
  });
});

document.getElementById("refresh-btn").addEventListener("click", loadAll);

document.getElementById("shops-search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  renderShopsFiltered(q ? allShops.filter((s) => matchShop(s, q)) : allShops);
});

document.getElementById("shops-include-deleted").addEventListener("change", async (e) => {
  includeDeletedShops = e.target.checked;
  await loadShops();
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
