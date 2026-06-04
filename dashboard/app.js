// ── State ──────────────────────────────────────────────────────────────────────
let jwtToken = localStorage.getItem("shop_token") || "";
let currentShop = null;
let catalogOffset = 0;
const catalogLimit = 50;
let allCatalogItems = [];

const ORDER_STATUSES = ["new", "confirmed", "done", "cancelled"];
const STATUS_LABELS  = { new: "Новый", confirmed: "Подтверждён", done: "Выполнен", cancelled: "Отменён" };
const STATUS_CLASS   = { new: "status-new", confirmed: "status-confirmed", done: "status-done", cancelled: "status-cancelled" };
const TAB_META = {
  overview:     ["Обзор",             "Статистика и активность вашего магазина"],
  catalog:      ["Каталог",           "Управление товарами и импорт CSV"],
  orders:       ["Заказы",            "Заявки от клиентов"],
  messages:     ["Диалоги",           "История переписки с клиентами"],
  bot:          ["Настройки бота",    "Промпт и параметры вашего ИИ-консультанта"],
  subscription: ["Подписка",          "Статус и лимиты вашего тарифа"],
  profile:      ["Профиль",           "Информация о магазине и настройки безопасности"],
};

// ── DOM refs ───────────────────────────────────────────────────────────────────
const loginScreen    = document.getElementById("login-screen");
const registerScreen = document.getElementById("register-screen");
const appEl          = document.getElementById("app");
const toastEl        = document.getElementById("toast");
let toastTimer       = null;

// ── Toast ──────────────────────────────────────────────────────────────────────
function showToast(msg, type = "info") {
  toastEl.textContent = msg;
  toastEl.className = `toast toast-${type}`;
  toastEl.classList.remove("hidden");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 3400);
}

// ── API helpers ────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (jwtToken) headers["Authorization"] = `Bearer ${jwtToken}`;
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { logout(); throw new Error("Сессия истекла"); }
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const d = await res.json(); msg = d.detail || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

async function patchApi(path, body) {
  return api(path, { method: "PATCH", json: body });
}

// ── Auth ───────────────────────────────────────────────────────────────────────
const forgotScreen = document.getElementById("forgot-screen");
const resetScreen  = document.getElementById("reset-screen");

function showLogin() {
  registerScreen.classList.add("hidden");
  forgotScreen.classList.add("hidden");
  resetScreen.classList.add("hidden");
  appEl.classList.add("hidden");
  loginScreen.classList.remove("hidden");
}

function showRegister() {
  loginScreen.classList.add("hidden");
  registerScreen.classList.remove("hidden");
}

function showForgot() {
  loginScreen.classList.add("hidden");
  forgotScreen.classList.remove("hidden");
}

function logout() {
  localStorage.removeItem("shop_token");
  jwtToken = "";
  currentShop = null;
  appEl.classList.add("hidden");
  showLogin();
}

async function doLogin(email, password) {
  const res = await fetch("/shop/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const ct = res.headers.get("content-type") || "";
  let data = {};
  if (ct.includes("application/json")) data = await res.json();
  if (!res.ok) throw new Error(data.detail || `Ошибка входа (${res.status})`);
  jwtToken = data.token;
  localStorage.setItem("shop_token", jwtToken);
}

async function enterApp() {
  loginScreen.classList.add("hidden");
  registerScreen.classList.add("hidden");
  appEl.classList.remove("hidden");
  await loadAll();
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.id === `tab-${name}`));
  const [title, sub] = TAB_META[name] || [name, ""];
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = sub;
}

// ── Stats ──────────────────────────────────────────────────────────────────────
function renderStats(data) {
  const labels = { sneakers: "Товары", orders: "Заказы", conversations: "Диалоги", messages: "Сообщения" };
  document.getElementById("stats-grid").innerHTML = Object.entries(labels).map(([k, label]) => `
    <article class="stat-card">
      <span>${label}</span>
      <strong>${data[k] ?? 0}</strong>
    </article>
  `).join("");
  renderSubBanner(data.subscription);
}

function renderSubBanner(sub) {
  const el = document.getElementById("sub-banner");
  if (!sub) { el.innerHTML = ""; return; }
  const isExpired = sub.status !== "active";
  const plan = sub.plan || "trial";
  const cls = isExpired ? "expired" : (plan === "trial" ? "trial" : "active");
  const ends = sub.trial_ends_at || sub.period_ends_at;
  const endStr = ends ? ` · до ${new Date(ends).toLocaleDateString("ru-RU")}` : "";
  el.innerHTML = `<div class="sub-banner ${cls}">
    <span>Тариф: <strong>${plan.toUpperCase()}</strong>${endStr}</span>
    <span>Лимит: ${sub.messages_limit} сообщений</span>
  </div>`;
}

// ── Messages ───────────────────────────────────────────────────────────────────
function renderMessages(items, targetId) {
  const root = document.getElementById(targetId);
  if (!items.length) { root.innerHTML = `<div class="panel-body muted small">Сообщений пока нет</div>`; return; }
  root.innerHTML = items.map(m => `
    <article class="message-item">
      <div class="message-meta">
        <strong>${esc(m.channel || "—")}</strong>
        <span>${esc(m.external_user_id || "—")}</span>
        <span class="role-${m.role}">${esc(m.role || "—")}</span>
        <span>${fmtDate(m.created_at)}</span>
      </div>
      <p>${esc(m.content || "")}</p>
    </article>
  `).join("");
}

// ── Catalog ────────────────────────────────────────────────────────────────────
function renderCatalog(data) {
  allCatalogItems = data.items;
  document.getElementById("catalog-count").textContent = `${data.count} позиций`;
  document.getElementById("cat-page-info").textContent =
    `${catalogOffset + 1}–${Math.min(catalogOffset + data.items.length, data.count)} из ${data.count}`;
  document.getElementById("cat-prev").disabled = catalogOffset <= 0;
  document.getElementById("cat-next").disabled = catalogOffset + catalogLimit >= data.count;
  const q = document.getElementById("catalog-search").value.trim().toLowerCase();
  renderCatalogFiltered(q ? allCatalogItems.filter(p => matchProduct(p, q)) : allCatalogItems);
}

function matchProduct(p, q) {
  return ["brand","model","colorway","category"].some(f => (p[f] || "").toLowerCase().includes(q));
}

function renderCatalogFiltered(items) {
  document.getElementById("catalog-body").innerHTML = items.map(p => `
    <tr data-id="${p.id}">
      <td>${esc(p.brand)}</td>
      <td>${esc(p.model)}</td>
      <td>${esc(p.colorway || "—")}</td>
      <td>${esc(p.size)}</td>
      <td class="editable-cell" data-field="quantity" data-value="${p.quantity}">${esc(p.quantity)}</td>
      <td class="editable-cell" data-field="price" data-value="${p.price}">${fmtPrice(p.price)}</td>
      <td>${esc(p.category || "—")}</td>
    </tr>
  `).join("") || `<tr><td colspan="7" class="muted center">Каталог пуст</td></tr>`;
  attachEditListeners();
}

function attachEditListeners() {
  document.querySelectorAll("#catalog-body .editable-cell").forEach(c => c.addEventListener("click", startEdit));
}

function startEdit(e) {
  const cell = e.currentTarget;
  if (cell.querySelector("input")) return;
  const field = cell.dataset.field;
  const orig  = cell.dataset.value;
  const input = document.createElement("input");
  input.type = "number"; input.min = field === "price" ? "1" : "0";
  input.value = orig; input.className = "inline-input";
  cell.textContent = ""; cell.appendChild(input);
  input.focus(); input.select();
  input.addEventListener("keydown", ev => {
    if (ev.key === "Enter") { ev.preventDefault(); commitEdit(cell, input, field, orig); }
    if (ev.key === "Escape") { ev.preventDefault(); cancelEdit(cell, field, orig); }
  });
  input.addEventListener("blur", () => commitEdit(cell, input, field, orig));
}

async function commitEdit(cell, input, field, orig) {
  const val = parseInt(input.value, 10);
  if (isNaN(val) || val === parseInt(orig, 10)) { cancelEdit(cell, field, orig); return; }
  const id = cell.closest("tr").dataset.id;
  cell.innerHTML = `<span class="saving">…</span>`;
  try {
    await patchApi(`/shop/products/${id}`, { [field]: val });
    cell.dataset.value = val;
    cell.textContent = field === "price" ? fmtPrice(val) : String(val);
    cell.addEventListener("click", startEdit);
    showToast("Сохранено", "success");
  } catch (err) {
    cancelEdit(cell, field, orig);
    showToast(err.message, "error");
  }
}

function cancelEdit(cell, field, orig) {
  cell.textContent = field === "price" ? fmtPrice(orig) : String(orig);
  cell.addEventListener("click", startEdit);
}

// ── Orders ─────────────────────────────────────────────────────────────────────
function renderOrders(data) {
  document.getElementById("orders-count").textContent = `${data.count} заказов`;
  document.getElementById("orders-body").innerHTML = data.items.map(o => `
    <tr>
      <td>#${esc(o.id)}</td>
      <td>${esc(o.channel || "—")}</td>
      <td>${esc(o.external_user_id || "—")}</td>
      <td>${esc(o.customer_name || "—")}</td>
      <td>${esc(o.customer_phone || "—")}</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(o.product_interest || "—")}</td>
      <td>
        <select class="status-select ${STATUS_CLASS[o.status]||"status-new"}" data-id="${o.id}" data-current="${o.status}">
          ${ORDER_STATUSES.map(s => `<option value="${s}" ${s===o.status?"selected":""}>${STATUS_LABELS[s]}</option>`).join("")}
        </select>
      </td>
      <td>${fmtDate(o.created_at)}</td>
    </tr>
  `).join("") || `<tr><td colspan="8" class="muted center">Заказов пока нет</td></tr>`;
  document.querySelectorAll(".status-select").forEach(sel => sel.addEventListener("change", handleStatus));
}

async function handleStatus(e) {
  const sel = e.currentTarget;
  const prev = sel.dataset.current;
  sel.disabled = true;
  try {
    await patchApi(`/shop/orders/${sel.dataset.id}`, { status: sel.value });
    sel.dataset.current = sel.value;
    sel.className = `status-select ${STATUS_CLASS[sel.value]||"status-new"}`;
    showToast(`Заказ #${sel.dataset.id}: ${STATUS_LABELS[sel.value]}`, "success");
  } catch (err) {
    sel.value = prev;
    showToast(err.message, "error");
  } finally {
    sel.disabled = false;
  }
}

// ── Bot settings ───────────────────────────────────────────────────────────────
function fillBotSettings(shop) {
  document.getElementById("shop-name-input").value = shop.name || "";
  document.getElementById("bot-prompt-input").value = shop.groq_system_prompt || "";
  renderTgStatus(shop.has_tg_bot, shop.tg_bot_username || null);
  renderMsStatus(shop.has_moysklad || false);
  renderSyncApiKey(shop.sync_api_key || null);
}

function renderTgStatus(connected, username) {
  const badge       = document.getElementById("tg-status-badge");
  const connBlock   = document.getElementById("tg-connected-block");
  const connectBlock = document.getElementById("tg-connect-block");
  if (connected) {
    badge.textContent = "Подключён";
    badge.className   = "status-badge badge-active";
    connBlock.classList.remove("hidden");
    connectBlock.classList.add("hidden");
    document.getElementById("tg-bot-username").textContent =
      username ? `@${username}` : "Бот активен";
  } else {
    badge.textContent = "Не подключён";
    badge.className   = "status-badge badge-pending";
    connBlock.classList.add("hidden");
    connectBlock.classList.remove("hidden");
  }
}

function renderMsStatus(connected) {
  const badge        = document.getElementById("ms-status-badge");
  const connBlock    = document.getElementById("ms-connected-block");
  const connectBlock = document.getElementById("ms-connect-block");
  if (!badge) return;
  if (connected) {
    badge.textContent = "Подключён";
    badge.className   = "status-badge badge-active";
    connBlock.classList.remove("hidden");
    connectBlock.classList.add("hidden");
  } else {
    badge.textContent = "Не подключён";
    badge.className   = "status-badge badge-pending";
    connBlock.classList.add("hidden");
    connectBlock.classList.remove("hidden");
    const wh = document.getElementById("ms-webhook-url");
    if (wh) wh.textContent = `${location.origin}/sync/moysklad`;
  }
}

function renderSyncApiKey(key) {
  const el = document.getElementById("sync-api-key-block");
  if (!el) return;
  if (key) {
    el.innerHTML = `
      <div class="req-row">
        <span class="req-label">API ключ</span>
        <code class="mono req-value" id="sync-key-value">${esc(key)}</code>
      </div>
      <p class="muted small">Храните ключ в безопасном месте. Нажмите "Сгенерировать" чтобы создать новый (старый перестанет работать).</p>
    `;
  } else {
    el.innerHTML = `<p class="muted small">Ключ не сгенерирован. Нажмите кнопку ниже.</p>`;
  }
  // Update 1С setup panel with URL and password
  const baseUrl = window.location.origin;
  const urlEl = document.getElementById("onec-url");
  const pwdEl = document.getElementById("onec-password");
  if (urlEl) urlEl.textContent = `${baseUrl}/sync/1c-exchange`;
  if (pwdEl) pwdEl.textContent = key || "— (сгенерируйте ключ выше)";
}

// ── МойСклад connect ───────────────────────────────────────────────────────────
document.getElementById("ms-connect-btn").addEventListener("click", async () => {
  const token = document.getElementById("ms-token-input").value.trim();
  const errEl = document.getElementById("ms-connect-error");
  errEl.classList.add("hidden");
  if (!token) { errEl.textContent = "Введите токен"; errEl.classList.remove("hidden"); return; }
  try {
    await api("/shop/moysklad-connect", { method: "POST", json: { token } });
    renderMsStatus(true);
    showToast("МойСклад подключён!", "success");
    document.getElementById("ms-token-input").value = "";
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

document.getElementById("ms-disconnect-btn").addEventListener("click", async () => {
  if (!confirm("Отключить МойСклад? Автосинхронизация остановится.")) return;
  try {
    await api("/shop/moysklad-connect", { method: "DELETE" });
    renderMsStatus(false);
    showToast("МойСклад отключён", "info");
  } catch (err) { showToast(err.message, "error"); }
});

document.getElementById("generate-api-key-btn").addEventListener("click", async () => {
  if (document.getElementById("sync-key-value") &&
      !confirm("Сгенерировать новый ключ? Старый перестанет работать.")) return;
  try {
    const r = await api("/shop/sync-api-key", { method: "POST" });
    renderSyncApiKey(r.api_key);
    showToast("API ключ сгенерирован", "success");
  } catch (err) { showToast(err.message, "error"); }
});

// ── Profile ────────────────────────────────────────────────────────────────────
const SHOP_STATUS_LABELS = { active: "Активен", pending: "На модерации", suspended: "Заблокирован", rejected: "Отклонён" };
const SHOP_STATUS_CLASS  = { active: "badge-active", pending: "badge-pending", suspended: "badge-error", rejected: "badge-error" };

function renderProfile(shop) {
  if (!shop) return;
  document.getElementById("profile-shop-id").textContent  = shop.id || "—";
  document.getElementById("profile-slug").textContent     = shop.slug || "—";
  document.getElementById("profile-email").textContent    = shop.owner_email || "—";
  document.getElementById("profile-name-input").value     = shop.name || "";
  document.getElementById("profile-email-input").value    = shop.owner_email || "";
  const st = shop.status || "active";
  document.getElementById("profile-status").innerHTML =
    `<span class="status-badge ${SHOP_STATUS_CLASS[st] || "badge-pending"}">${SHOP_STATUS_LABELS[st] || st}</span>`;
}

// ── Subscription ───────────────────────────────────────────────────────────────
function renderSubscription(sub) {
  if (!sub) {
    document.getElementById("subscription-card").innerHTML = `<div class="panel subscription-card"><p class="muted">Подписка не найдена</p></div>`;
    return;
  }
  const plan = sub.plan || "trial";
  const cls  = sub.status !== "active" ? "plan-expired" : (plan === "trial" ? "plan-trial" : "plan-active");
  const ends = sub.trial_ends_at || sub.period_ends_at;
  document.getElementById("subscription-card").innerHTML = `
    <div class="subscription-card">
      <h2>Ваша подписка</h2>
      <div class="subscription-plan">
        <span class="plan-badge ${cls}">${plan.toUpperCase()}</span>
        <span class="muted">${sub.status === "active" ? "Активна" : "Истекла"}</span>
      </div>
      <div class="sub-details">
        <div class="sub-detail"><div class="label">Лимит сообщений</div><div class="value">${sub.messages_limit}</div></div>
        <div class="sub-detail"><div class="label">Каналов</div><div class="value">${sub.channels_limit}</div></div>
        ${ends ? `<div class="sub-detail"><div class="label">Действует до</div><div class="value" style="font-size:15px">${new Date(ends).toLocaleDateString("ru-RU")}</div></div>` : ""}
      </div>
      <div class="payment-info">
        <h3>Как оплатить</h3>
        <div class="payment-plans">
          <div class="payment-plan">
            <div class="plan-name">Basic</div>
            <div class="plan-price">$29 / мес</div>
            <div class="plan-desc">2 000 сообщений</div>
          </div>
          <div class="payment-plan payment-plan-featured">
            <div class="plan-name">Pro</div>
            <div class="plan-price">$79 / мес</div>
            <div class="plan-desc">Безлимит</div>
          </div>
        </div>
        <div id="payment-details-block"></div>
      </div>
    </div>
  `;
}

function renderPaymentInfo(info) {
  const el = document.getElementById("payment-details-block");
  if (!el) return;
  if (!info || (!info.kaspi && !info.details)) {
    el.innerHTML = `<p class="muted small">Для оплаты свяжитесь с поддержкой.</p>`;
    return;
  }
  el.innerHTML = `
    <div class="payment-requisites">
      ${info.kaspi ? `<div class="req-row"><span class="req-label">Kaspi Gold</span><span class="req-value mono">${esc(info.kaspi)}</span></div>` : ""}
      ${info.details ? `<p class="req-note">${esc(info.details)}</p>` : ""}
    </div>
  `;
}

// ── CSV import ─────────────────────────────────────────────────────────────────
function renderImportResult(result) {
  const el = document.getElementById("import-result");
  el.classList.remove("hidden");
  if (!result.valid) {
    el.innerHTML = `<p><strong>Ошибки (${result.error_count}):</strong></p>
      <ul class="error-list">${result.errors.map(e => `<li>${esc(e)}</li>`).join("")}</ul>`;
    return;
  }
  const rows = (result.preview || []).map(p => `<tr><td>${esc(p.brand)}</td><td>${esc(p.model)}</td><td>${esc(p.size)}</td><td>${esc(p.quantity)}</td><td>${fmtPrice(p.price)}</td></tr>`).join("");
  el.innerHTML = `<p><strong>Файл валиден:</strong> ${result.valid_rows} строк</p>
    <table class="preview-table"><thead><tr><th>Бренд</th><th>Модель</th><th>Р-р</th><th>Qty</th><th>Цена</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function uploadCsv(path) {
  const file = document.getElementById("csv-file").files?.[0];
  if (!file) { showToast("Выберите CSV файл", "error"); return null; }
  const form = new FormData(); form.append("file", file);
  const headers = {};
  if (jwtToken) headers["Authorization"] = `Bearer ${jwtToken}`;
  const res = await fetch(path, { method: "POST", headers, body: form });
  if (res.status === 401) { logout(); throw new Error("Сессия истекла"); }
  if (!res.ok) { const t = await res.text(); throw new Error(t); }
  return res.json();
}

// ── Analytics charts ───────────────────────────────────────────────────────────
let _charts = {};

function destroyChart(id) {
  if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
}

function renderAnalytics(data) {
  if (!data || data.error) return;

  // 1. Messages per day — line chart
  destroyChart("messages");
  const msgCtx = document.getElementById("chart-messages");
  if (msgCtx) {
    const labels = data.daily_messages.map(r => r.day.slice(5)); // MM-DD
    const values = data.daily_messages.map(r => r.cnt);
    _charts["messages"] = new Chart(msgCtx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Сообщений",
          data: values,
          borderColor: "#2563eb",
          backgroundColor: "rgba(37,99,235,0.08)",
          tension: 0.3,
          fill: true,
          pointRadius: 3,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  // 2. Top brands — horizontal bar chart
  destroyChart("brands");
  const brandCtx = document.getElementById("chart-brands");
  if (brandCtx && data.top_brands.length) {
    _charts["brands"] = new Chart(brandCtx, {
      type: "bar",
      data: {
        labels: data.top_brands.map(r => r.brand),
        datasets: [{
          label: "SKU в наличии",
          data: data.top_brands.map(r => r.cnt),
          backgroundColor: [
            "#2563eb","#7c3aed","#db2777","#ea580c",
            "#16a34a","#0891b2","#d97706","#64748b",
          ],
          borderRadius: 6,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  // 3. Orders by status — doughnut chart
  destroyChart("orders");
  const ordCtx = document.getElementById("chart-orders");
  if (ordCtx) {
    const statusLabels = { new: "Новый", confirmed: "Подтверждён", done: "Выполнен", cancelled: "Отменён" };
    const entries = Object.entries(data.order_stats || {});
    if (entries.length) {
      _charts["orders"] = new Chart(ordCtx, {
        type: "doughnut",
        data: {
          labels: entries.map(([s]) => statusLabels[s] || s),
          datasets: [{
            data: entries.map(([, v]) => v),
            backgroundColor: ["#2563eb","#16a34a","#7c3aed","#dc2626"],
            borderWidth: 0,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { font: { size: 12 } } } },
          cutout: "65%",
        },
      });
    } else {
      ordCtx.parentElement.innerHTML = `<p class="muted center" style="padding:60px 0">Заказов пока нет</p>`;
    }
  }

  // 4. Top models — bar list
  const modelsEl = document.getElementById("top-models-list");
  if (modelsEl && data.top_models.length) {
    const max = data.top_models[0].stock || 1;
    modelsEl.innerHTML = data.top_models.map(r => `
      <div class="top-list-item">
        <span class="top-list-label">${esc(r.name)}</span>
        <div class="top-list-bar-wrap">
          <div class="top-list-bar" style="width:${Math.round(r.stock/max*100)}%"></div>
        </div>
        <span class="top-list-value">${r.stock} шт</span>
      </div>
    `).join("");
  } else if (modelsEl) {
    modelsEl.innerHTML = `<p class="muted center" style="padding:40px 0">Товаров нет</p>`;
  }
}

// ── Load all ───────────────────────────────────────────────────────────────────
async function loadAll() {
  try {
    const [me, stats, products, orders, messages, sub, payInfo, analytics] = await Promise.all([
      api("/shop/me"),
      api("/shop/stats"),
      api(`/shop/products?limit=${catalogLimit}&offset=${catalogOffset}`),
      api("/shop/orders?limit=100"),
      api("/shop/messages?limit=40"),
      api("/shop/subscription").catch(() => null),
      api("/shop/payment-info").catch(() => null),
      api("/shop/analytics/overview").catch(() => null),
    ]);
    currentShop = me;
    document.getElementById("shop-name-sidebar").textContent = me.name || "Магазин";
    document.getElementById("shop-email-sidebar").textContent = me.owner_email || "";
    document.getElementById("export-link").href = "#";
    document.getElementById("export-link").onclick = async (e) => {
      e.preventDefault();
      const headers = {}; if (jwtToken) headers["Authorization"] = `Bearer ${jwtToken}`;
      const res = await fetch("/shop/export", { headers });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      Object.assign(document.createElement("a"), { href: url, download: "catalog.csv" }).click();
    };
    renderStats(stats);
    renderMessages(messages.items || [], "overview-messages");
    renderMessages(messages.items || [], "messages-list");
    renderCatalog(products);
    renderOrders(orders);
    fillBotSettings(me);
    renderSubscription(sub);
    renderPaymentInfo(payInfo);
    renderProfile(me);
    renderAnalytics(analytics);
  } catch (err) {
    showToast(err.message || "Ошибка загрузки", "error");
  }
}

// ── Event bindings ─────────────────────────────────────────────────────────────
document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("email-input").value.trim();
  const pwd   = document.getElementById("password-input").value;
  const errEl = document.getElementById("login-error");
  errEl.classList.add("hidden");
  try {
    await doLogin(email, pwd);
    await enterApp();
  } catch (err) {
    errEl.textContent = err.message || "Ошибка входа";
    errEl.classList.remove("hidden");
  }
});

document.getElementById("logout-btn").addEventListener("click", logout);
document.getElementById("refresh-btn").addEventListener("click", loadAll);

document.querySelectorAll(".nav-item").forEach(btn =>
  btn.addEventListener("click", () => switchTab(btn.dataset.tab))
);

document.getElementById("cat-prev").addEventListener("click", async () => {
  catalogOffset = Math.max(0, catalogOffset - catalogLimit);
  const data = await api(`/shop/products?limit=${catalogLimit}&offset=${catalogOffset}`);
  renderCatalog(data);
});
document.getElementById("cat-next").addEventListener("click", async () => {
  catalogOffset += catalogLimit;
  const data = await api(`/shop/products?limit=${catalogLimit}&offset=${catalogOffset}`);
  renderCatalog(data);
});

document.getElementById("catalog-search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  renderCatalogFiltered(q ? allCatalogItems.filter(p => matchProduct(p, q)) : allCatalogItems);
});

document.getElementById("preview-btn").addEventListener("click", async () => {
  try { renderImportResult(await uploadCsv("/shop/import-preview")); }
  catch (err) { showToast(err.message, "error"); }
});
document.getElementById("import-btn").addEventListener("click", async () => {
  try {
    const r = await uploadCsv("/shop/import");
    showToast(`Импортировано: ${r.imported}`, "success"); loadAll();
  } catch (err) { showToast(err.message, "error"); }
});
document.getElementById("replace-btn").addEventListener("click", async () => {
  if (!confirm("Заменить весь каталог? Старые товары будут удалены.")) return;
  try {
    const r = await uploadCsv("/shop/import?replace=true");
    showToast(`Каталог заменён: ${r.imported} позиций`, "success"); loadAll();
  } catch (err) { showToast(err.message, "error"); }
});

// ── Copy-on-click for .copy-on-click elements ──────────────────────────────────
document.addEventListener("click", (e) => {
  const el = e.target.closest(".copy-on-click");
  if (!el) return;
  const text = el.textContent.trim();
  if (!text || text.startsWith("—")) return;
  navigator.clipboard.writeText(text).then(() => showToast("Скопировано", "success"));
});

// ── 1С XML import ─────────────────────────────────────────────────────────────
async function upload1cXml(replace) {
  const fileInput = document.getElementById("xml-1c-file");
  const resultEl = document.getElementById("xml-1c-result");
  if (!fileInput.files.length) { showToast("Выберите .xml файл", "error"); return; }
  const file = fileInput.files[0];
  if (!file.name.toLowerCase().endsWith(".xml")) { showToast("Нужен .xml файл", "error"); return; }

  const fd = new FormData();
  fd.append("file", file);
  const headers = {};
  if (jwtToken) headers["Authorization"] = `Bearer ${jwtToken}`;
  // Pass sync_api_key from currentShop for /sync/1c endpoint
  if (currentShop && currentShop.sync_api_key) {
    headers["X-API-Key"] = currentShop.sync_api_key;
  } else {
    showToast("Сначала сгенерируйте Sync API Key в Боте → Настройки", "error");
    return;
  }

  resultEl.classList.add("hidden");
  try {
    const res = await fetch(`/sync/1c?replace=${replace}`, { method: "POST", headers, body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Ошибка загрузки");
    resultEl.textContent = `Готово: разобрано ${data.parsed} позиций, обновлено ${data.imported}.`;
    resultEl.classList.remove("hidden");
    showToast(`1С: обновлено ${data.imported} позиций`, "success");
    loadAll();
  } catch (err) {
    showToast(err.message, "error");
    resultEl.textContent = err.message;
    resultEl.classList.remove("hidden");
  }
}

document.getElementById("xml-1c-btn").addEventListener("click", () => upload1cXml(false));
document.getElementById("xml-1c-replace-btn").addEventListener("click", async () => {
  if (!confirm("Заменить весь каталог данными из 1С? Старые товары будут удалены.")) return;
  await upload1cXml(true);
});

document.getElementById("save-bot-settings").addEventListener("click", async () => {
  try {
    await patchApi("/shop/settings", {
      name: document.getElementById("shop-name-input").value.trim() || null,
      groq_system_prompt: document.getElementById("bot-prompt-input").value.trim() || null,
    });
    showToast("Настройки сохранены", "success");
    currentShop = await api("/shop/me");
    document.getElementById("shop-name-sidebar").textContent = currentShop.name;
  } catch (err) { showToast(err.message, "error"); }
});


// ── Helpers ────────────────────────────────────────────────────────────────────
function esc(v) {
  return String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
}
function fmtPrice(v) { return `${Number(v||0).toLocaleString("ru-RU")} ₸`; }
function fmtDate(v) {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d) ? String(v) : d.toLocaleString("ru-RU");
}

// ── Telegram bot ───────────────────────────────────────────────────────────────
document.getElementById("tg-connect-btn").addEventListener("click", async () => {
  const token  = document.getElementById("tg-token-input").value.trim();
  const errEl  = document.getElementById("tg-connect-error");
  errEl.classList.add("hidden");
  if (!token) { errEl.textContent = "Вставьте токен"; errEl.classList.remove("hidden"); return; }
  try {
    const r = await api("/shop/bot-connect", { method: "POST", json: { tg_token: token } });
    document.getElementById("tg-token-input").value = "";
    currentShop = await api("/shop/me");
    renderTgStatus(true, r.bot_username);
    showToast(`Бот @${r.bot_username || "?"} подключён!`, "success");
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

document.getElementById("tg-disconnect-btn").addEventListener("click", async () => {
  if (!confirm("Отключить Telegram бота? Бот перестанет отвечать клиентам.")) return;
  try {
    await api("/shop/bot-connect", { method: "DELETE" });
    currentShop = await api("/shop/me");
    renderTgStatus(false, null);
    showToast("Бот отключён", "info");
  } catch (err) { showToast(err.message, "error"); }
});

// ── Profile form ───────────────────────────────────────────────────────────────
document.getElementById("save-profile-btn").addEventListener("click", async () => {
  const name  = document.getElementById("profile-name-input").value.trim() || null;
  const email = document.getElementById("profile-email-input").value.trim() || null;
  try {
    await patchApi("/shop/settings", { name });
    if (email && currentShop && email !== currentShop.owner_email) {
      await api("/shop/change-email", { method: "POST", json: { email } });
    }
    currentShop = await api("/shop/me");
    renderProfile(currentShop);
    document.getElementById("shop-name-sidebar").textContent = currentShop.name || "Магазин";
    document.getElementById("shop-email-sidebar").textContent = currentShop.owner_email || "";
    showToast("Профиль обновлён", "success");
  } catch (err) { showToast(err.message, "error"); }
});

document.getElementById("profile-change-pwd-btn").addEventListener("click", async () => {
  const cur = document.getElementById("profile-cur-pwd").value;
  const nw  = document.getElementById("profile-new-pwd").value;
  if (!cur || !nw) { showToast("Заполните оба поля", "error"); return; }
  try {
    await api("/shop/change-password", { method: "POST", json: { current_password: cur, new_password: nw } });
    document.getElementById("profile-cur-pwd").value = "";
    document.getElementById("profile-new-pwd").value = "";
    showToast("Пароль изменён", "success");
  } catch (err) { showToast(err.message, "error"); }
});

// ── Register form ──────────────────────────────────────────────────────────────
document.getElementById("show-register").addEventListener("click", showRegister);
document.getElementById("show-login").addEventListener("click", showLogin);

// ── Forgot password ────────────────────────────────────────────────────────────
document.getElementById("show-forgot").addEventListener("click", showForgot);
document.getElementById("show-login-from-forgot").addEventListener("click", showLogin);

document.getElementById("forgot-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email  = document.getElementById("forgot-email").value.trim();
  const errEl  = document.getElementById("forgot-error");
  const okEl   = document.getElementById("forgot-success");
  const btn    = e.target.querySelector("button[type=submit]");
  errEl.classList.add("hidden");
  okEl.classList.add("hidden");
  btn.disabled = true;
  try {
    const res = await fetch("/shop/forgot-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `Ошибка ${res.status}`);
    okEl.textContent = data.message || "Письмо отправлено! Проверьте почту.";
    okEl.classList.remove("hidden");
  } catch (err) {
    errEl.textContent = err.message || "Ошибка сервера, попробуйте позже";
    errEl.classList.remove("hidden");
  } finally {
    btn.disabled = false;
  }
});

// ── Reset password (from email link) ──────────────────────────────────────────
(function checkResetToken() {
  const params = new URLSearchParams(window.location.search);
  const token  = params.get("token");
  if (!token) return;
  loginScreen.classList.add("hidden");
  resetScreen.classList.remove("hidden");

  document.getElementById("reset-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pwd   = document.getElementById("reset-password").value;
    const pwd2  = document.getElementById("reset-password2").value;
    const errEl = document.getElementById("reset-error");
    const okEl  = document.getElementById("reset-success");
    const btn   = e.target.querySelector("button[type=submit]");
    errEl.classList.add("hidden");
    okEl.classList.add("hidden");
    if (pwd !== pwd2) {
      errEl.textContent = "Пароли не совпадают";
      errEl.classList.remove("hidden");
      return;
    }
    btn.disabled = true;
    try {
      const res = await fetch("/shop/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password: pwd }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Ошибка");
      okEl.textContent = "Пароль изменён! Теперь войдите с новым паролем.";
      okEl.classList.remove("hidden");
      btn.disabled = true;
      setTimeout(() => {
        history.replaceState({}, "", "/shop");
        showLogin();
      }, 2500);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
      btn.disabled = false;
    }
  });
})();

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name     = document.getElementById("reg-name").value.trim();
  const email    = document.getElementById("reg-email").value.trim();
  const password = document.getElementById("reg-password").value;
  const errEl    = document.getElementById("register-error");
  const okEl     = document.getElementById("register-success");
  errEl.classList.add("hidden");
  okEl.classList.add("hidden");

  try {
    const res = await fetch("/shop/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shop_name: name, email, password }),
    });
    let data = {};
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      data = await res.json();
    }
    if (!res.ok) throw new Error(data.detail || `Ошибка сервера (${res.status})`);
    okEl.textContent = data.message || "Заявка отправлена! Ожидайте активации.";
    okEl.classList.remove("hidden");
    document.getElementById("register-form").reset();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

// ── Init ───────────────────────────────────────────────────────────────────────
if (jwtToken) {
  enterApp().catch(() => {
    localStorage.removeItem("shop_token");
    jwtToken = "";
    showLogin();
  });
} else {
  showLogin();
}
