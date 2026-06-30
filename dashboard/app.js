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
  overview:     ["Обзор",       "Статистика и активность вашего магазина"],
  catalog:      ["Каталог",     "Управление товарами и импорт CSV"],
  orders:       ["Заказы",      "Заявки от клиентов"],
  messages:     ["Диалоги",     "История переписки с клиентами"],
  analytics:    ["Аналитика",   "Подробная статистика и метрики"],
  bot:          ["Агент",       "Настройка и управление вашим ИИ-агентом"],
  subscription: ["Подписка",    "Статус и лимиты вашего тарифа"],
  profile:      ["Профиль",     "Информация о магазине и настройки безопасности"],
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
  // Chart.js needs a tick after tab becomes visible to measure container width
  if (name === "analytics" || name === "overview") {
    setTimeout(() => Object.values(_charts).forEach(c => c?.resize?.()), 60);
  }
  if (name === "bot" && currentShop) showAgentCard(currentShop);
}

// ── Stats ──────────────────────────────────────────────────────────────────────
function renderStats(data) {
  const CFG = [
    {
      k: "products", label: "Товары",
      bg: "rgba(59,130,246,0.13)", color: "#3B82F6",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><line x1="3" x2="21" y1="6" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>`,
    },
    {
      k: "orders", label: "Заказы",
      bg: "rgba(34,197,94,0.13)", color: "#16a34a",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/></svg>`,
    },
    {
      k: "conversations", label: "Диалоги",
      bg: "rgba(249,115,22,0.13)", color: "#ea580c",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/></svg>`,
    },
    {
      k: "messages", label: "Сообщений",
      bg: "rgba(139,92,246,0.13)", color: "#8b5cf6",
      svg: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`,
    },
  ];
  const val = (k) => data[k] ?? data["sneakers"] ?? 0;
  document.getElementById("stats-grid").innerHTML = CFG.map(c => `
    <div class="stat-card" data-reveal>
      <div class="stat-card-text">
        <span class="stat-card-label">${c.label}</span>
        <span class="stat-card-num">${Number(val(c.k)).toLocaleString("ru-RU")}</span>
      </div>
      <span class="stat-icon-badge" style="background:${c.bg};color:${c.color}">${c.svg}</span>
    </div>
  `).join("");
  renderSubBanner(data.subscription);
}

// ── First-run setup checklist ──────────────────────────────────────────────────
function renderSetupChecklist(me, stats) {
  const panel = document.getElementById("setup-checklist");
  if (!panel || !me) return;
  const hasTg = !!me.has_tg_bot;
  const products = Number(stats?.products || 0);
  const hasPersona = !!((me.groq_system_prompt || "").trim() || (me.bot_role || "").trim());
  // Hide once the two hard requirements are met. Persona + sandbox are nudges, not gates.
  if (hasTg && products > 0) {
    panel.hidden = true;
    return;
  }
  const items = [
    {
      done: hasTg,
      title: "Подключите Telegram-бот",
      desc: "Создайте бота у @BotFather и вставьте токен в разделе «Агент».",
      cta: "Перейти к подключению",
      tab: "bot",
    },
    {
      done: products > 0,
      title: "Загрузите каталог",
      desc: "Импортируйте CSV или подключите МойСклад — бот отвечает только по вашим товарам.",
      cta: products > 0 ? `${products} товаров — открыть каталог` : "Загрузить каталог",
      tab: "catalog",
    },
    {
      done: hasPersona,
      title: "Настройте характер агента",
      desc: "Укажите роль и тон — например, «консультант мебельного магазина, на «вы»».",
      cta: "Настроить",
      tab: "bot",
    },
    {
      done: false,
      title: "Протестируйте бота в песочнице",
      desc: "На вкладке «Агент» — блок «Тестировать агента». Бесплатно, против вашего каталога.",
      cta: "Открыть песочницу",
      tab: "bot",
    },
  ];
  const doneCount = items.filter(i => i.done).length;
  document.getElementById("setup-progress").textContent = `${doneCount} из ${items.length} шагов`;
  document.getElementById("setup-checklist-items").innerHTML = items.map((it, idx) => {
    const checkColor = it.done ? "#16a34a" : "var(--text3,#9ca3af)";
    const titleStyle = it.done ? "text-decoration:line-through;opacity:0.7" : "font-weight:600";
    return `<div style="display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-top:${idx === 0 ? 'none' : '1px solid var(--border,#e5e7eb)'}">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="${checkColor}" stroke-width="2.5" style="flex-shrink:0;margin-top:1px">
        ${it.done ? '<polyline points="20 6 9 17 4 12"/>' : '<circle cx="12" cy="12" r="9"/>'}
      </svg>
      <div style="flex:1;min-width:0">
        <div style="${titleStyle};margin-bottom:2px">${esc(it.title)}</div>
        <div class="muted small" style="margin-bottom:6px">${esc(it.desc)}</div>
        <button class="btn ${it.done ? 'secondary' : ''}" style="font-size:12px;padding:6px 12px"
                data-setup-target="${it.tab}">${esc(it.cta)}</button>
      </div>
    </div>`;
  }).join("");
  panel.hidden = false;
  panel.querySelectorAll("[data-setup-target]").forEach(btn => {
    btn.addEventListener("click", () => {
      const nav = document.querySelector(`[data-tab=${btn.dataset.setupTarget}]`);
      nav?.click();
    });
  });
}

// ── Overview extras ────────────────────────────────────────────────────────────
function renderOverviewMiniChart(analyticsData) {
  destroyChart("overview-mini");
  const ctx = document.getElementById("chart-overview-mini");
  if (!ctx || !analyticsData?.daily_messages?.length) return;
  const days = analyticsData.daily_messages.slice(-7);
  const labels = days.map(r => {
    const d = new Date(r.day);
    return d.toLocaleDateString("ru-RU", { weekday: "short" });
  });
  const values = days.map(r => r.cnt);
  const hint = document.getElementById("ov-messages-hint");
  if (hint) hint.textContent = `Обработано агентом · ${values.reduce((a,b)=>a+b,0).toLocaleString("ru-RU")} сообщений`;
  const grad = ctx.getContext("2d").createLinearGradient(0, 0, 0, 200);
  grad.addColorStop(0, "rgba(59,130,246,0.22)");
  grad.addColorStop(1, "rgba(59,130,246,0)");
  _charts["overview-mini"] = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: values, borderColor: "#3B82F6", borderWidth: 3,
        backgroundColor: grad,
        tension: 0.4, fill: true, pointRadius: 4, pointHoverRadius: 6,
        pointBackgroundColor: "#fff", pointBorderColor: "#3B82F6", pointBorderWidth: 2.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: TOOLTIP_STYLE },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: chartTextColor() } },
        y: { beginAtZero: true, ticks: { precision: 0, color: chartTextColor(), maxTicksLimit: 4 },
             grid: { color: chartGridColor() }, border: { display: false } },
      },
    },
  });
}

// Shared chart theming helpers (respect light/dark)
function chartTextColor() { return document.body.classList.contains("sb-dark") ? "#9a9aa0" : "#86868b"; }
function chartGridColor() { return document.body.classList.contains("sb-dark") ? "#2a2a30" : "#f0f0f2"; }
const TOOLTIP_STYLE = {
  backgroundColor: "rgba(28,28,32,0.94)", padding: 11, cornerRadius: 10,
  titleFont: { size: 12, weight: "600" }, bodyFont: { size: 12 },
  displayColors: false, boxPadding: 4,
};

function renderOverviewAgent(me, stats) {
  const el = document.getElementById("overview-agent-card");
  if (!el) return;
  const role = me.bot_role || "Агент не настроен";
  const isOn = me.has_tg_bot;
  el.innerHTML = `
    <div class="ov-agent-item">
      <div class="ov-agent-icon" style="background:var(--accent-soft);color:var(--accent)"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg></div>
      <div class="ov-agent-info">
        <div class="ov-agent-name">${esc(me.name || "Агент")}</div>
        <div class="ov-agent-stat">${esc(role)}</div>
      </div>
      <div class="ov-agent-status" style="color:${isOn?"var(--success-text)":"var(--text3)"}">
        <span style="width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0"></span>${isOn ? "Активен" : "Не подключён"}
      </div>
    </div>
    <div style="margin:12px 0;border-top:1px solid var(--line2)"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
      <div style="text-align:center;padding:10px;background:var(--bg2);border-radius:12px">
        <div style="font-size:20px;font-weight:600;color:var(--text)">${(stats.messages??0).toLocaleString("ru-RU")}</div>
        <div style="font-size:12px;color:var(--text3)">сообщений</div>
      </div>
      <div style="text-align:center;padding:10px;background:var(--bg2);border-radius:12px">
        <div style="font-size:20px;font-weight:600;color:var(--text)">${(stats.orders??0).toLocaleString("ru-RU")}</div>
        <div style="font-size:12px;color:var(--text3)">заказов</div>
      </div>
    </div>
    <div style="margin-top:14px">
      <button class="btn secondary" style="width:100%;font-size:13px" onclick="document.querySelector('[data-tab=bot]').click()">
        Настроить агента →
      </button>
    </div>
  `;
}

function renderOverviewConversations(items) {
  const el = document.getElementById("overview-messages");
  if (!el) return;
  if (!items.length) {
    el.innerHTML = `<div style="padding:32px;text-align:center;color:var(--text3)">Диалогов пока нет</div>`;
    return;
  }
  const COLORS = ["#3B82F6","#6366f1","#10b981","#f97316","#e11d48"];
  el.innerHTML = items.slice(0,6).map((m,i) => {
    const uid = (m.external_user_id || "—").toString();
    const initials = uid.length >= 2 ? uid.slice(0,2).toUpperCase() : uid.toUpperCase();
    const col = COLORS[i % COLORS.length];
    const ch = m.channel || "—";
    const chIcon = channelIconSvg(ch);
    const preview = (m.content||"").slice(0,36) + ((m.content||"").length>36?"…":"");
    return `
      <div class="ov-table-row">
        <div class="ov-client">
          <div class="ov-avatar" style="background:${col}">${initials}</div>
          <div>
            <div class="ov-name">${esc(uid)}</div>
            <div style="font-size:12px;color:var(--text3)">${esc(preview)}</div>
          </div>
        </div>
        <div style="display:inline-flex;align-items:center;gap:6px">${chIcon}${esc(ch)}</div>
        <div><span class="role-${m.role}" style="font-size:12px">${esc(m.role||"—")}</span></div>
        <div style="color:var(--text3);font-size:12px">${fmtDate(m.created_at)}</div>
      </div>
    `;
  }).join("");
}

// ── Inline SVG icons (avoid emoji rendering issues across platforms) ────────────
const SB_ICONS = {
  telegram: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`,
  whatsapp: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`,
  message:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/></svg>`,
  warehouse:`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>`,
  building: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M8 10h.01M16 10h.01M12 6h.01M12 10h.01"/></svg>`,
  bag:      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><line x1="3" y1="6" x2="21" y2="6"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>`,
  zap:      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
};
function channelIconSvg(ch) {
  const key = ch === "telegram" ? "telegram" : ch === "whatsapp" ? "whatsapp" : "message";
  return `<span class="ch-ico">${SB_ICONS[key]}</span>`;
}

function renderOverviewIntegrations(me) {
  const el = document.getElementById("overview-integrations");
  if (!el) return;
  const ints = [
    { name:"Telegram", icon:SB_ICONS.telegram,  color:"#3B82F6", on: me.has_tg_bot },
    { name:"МойСклад", icon:SB_ICONS.warehouse,  color:"#16a34a", on: me.has_moysklad },
    { name:"1С",       icon:SB_ICONS.building,   color:"#ea580c", on: !!(me.sync_api_key) },
    { name:"WhatsApp", icon:SB_ICONS.whatsapp,   color:"#22c55e", on: false },
    { name:"Каталог",  icon:SB_ICONS.bag,        color:"#8b5cf6", on: true },
    { name:"API",      icon:SB_ICONS.zap,        color:"#3B82F6", on: !!(me.sync_api_key) },
  ];
  el.innerHTML = ints.map(i => `
    <div class="int-mini">
      <div class="int-icon" style="background:color-mix(in srgb, ${i.color} 13%, transparent);color:${i.color}">${i.icon}</div>
      <div class="int-name">${esc(i.name)}</div>
      <div class="int-dot ${i.on?"on":"off"}"></div>
    </div>
  `).join("");
}

function renderSubBanner(sub) {
  const el = document.getElementById("sub-banner");
  if (!sub) { el.innerHTML = ""; return; }
  const isExpired = sub.status !== "active";
  const plan = sub.plan || "trial";
  const cls = isExpired ? "expired" : (plan === "trial" ? "trial" : "active");
  const ends = sub.trial_ends_at || sub.period_ends_at;
  const endStr = ends ? ` · до ${new Date(ends).toLocaleDateString("ru-RU")}` : "";
  const used = sub.messages_used ?? 0;
  const limit = sub.messages_limit ?? 0;
  const limitStr = sub.unlimited || limit >= 999999
    ? `Использовано: ${used} (безлимит)`
    : `Сообщения: ${used} / ${limit}`;
  el.innerHTML = `<div class="sub-banner ${cls}">
    <span>Тариф: <strong>${plan.toUpperCase()}</strong>${endStr}</span>
    <span>${limitStr}</span>
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
  const attrs = typeof p.attributes === "object" ? JSON.stringify(p.attributes) : (p.attributes || "");
  return ["name","sku","category","description"].some(f => (p[f] || "").toLowerCase().includes(q))
    || attrs.toLowerCase().includes(q);
}

// Render the JSONB attributes (размер, цвет, материал, память…) read-only, so the
// owner sees exactly the facts the bot has per SKU. Keys are shop-defined — nothing
// is hardcoded per vertical.
function fmtAttrs(a) {
  if (a === null || a === undefined || a === "") return "—";
  let obj = a;
  if (typeof a === "string") {
    try { obj = JSON.parse(a); } catch { return esc(a); }
  }
  if (typeof obj !== "object" || Array.isArray(obj)) return esc(String(obj));
  const parts = Object.entries(obj)
    .filter(([, v]) => v !== null && v !== undefined && String(v).trim() !== "")
    .map(([k, v]) => `${esc(k)}: ${esc(String(v))}`);
  return parts.length ? parts.join(" · ") : "—";
}

function renderCatalogFiltered(items) {
  document.getElementById("catalog-body").innerHTML = items.map(p => `
    <tr data-id="${p.id}">
      <td>${esc(p.name)}</td>
      <td>${esc(p.sku || "—")}</td>
      <td>${esc(p.category || "—")}</td>
      <td class="editable-cell" data-field="quantity" data-value="${p.quantity}">${esc(p.quantity)}</td>
      <td class="editable-cell" data-field="price" data-value="${p.price}">${fmtPrice(p.price)}</td>
      <td class="attrs-cell">${fmtAttrs(p.attributes)}</td>
    </tr>
  `).join("") || `<tr><td colspan="6" class="muted center">Каталог пуст</td></tr>`;
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
const DATA_SOURCE_LABELS = {
  manual: "Вручную / CSV",
  csv: "CSV",
  moysklad: "МойСклад",
  "1c": "1С",
  api: "REST API",
};

function fillBotSettings(shop) {
  document.getElementById("shop-name-input").value = shop.name || "";
  const groqInput = document.getElementById("groq-key-input");
  const clearGroqBtn = document.getElementById("clear-groq-key-btn");
  if (groqInput) groqInput.value = "";
  if (clearGroqBtn) clearGroqBtn.classList.toggle("hidden", !shop.has_own_groq_key);
  document.getElementById("bot-role-input").value = shop.bot_role || "";
  document.getElementById("business-type-input").value = shop.business_type || "";
  document.getElementById("website-url-input").value = shop.website_url || "";
  document.getElementById("bot-prompt-input").value = shop.groq_system_prompt || "";
  const dsBadge = document.getElementById("data-source-badge");
  if (dsBadge) {
    const src = shop.data_source || "manual";
    dsBadge.textContent = `Источник: ${DATA_SOURCE_LABELS[src] || src}`;
  }
  const ownerTgInput = document.getElementById("owner-tg-id-input");
  if (ownerTgInput) ownerTgInput.value = shop.owner_telegram_chat_id || "";
  renderTgStatus(shop.has_tg_bot, shop.tg_bot_username || null, shop);
  renderMsStatus(shop.has_moysklad || false, shop.sync_api_key || null);
  renderSyncApiKey(shop.sync_api_key || null);
}

function renderTgNotifyHint(shop) {
  const notifyHint = document.getElementById("tg-notify-hint");
  if (!notifyHint) return;
  const chatId = (shop?.owner_telegram_chat_id || "").trim();
  if (!chatId) {
    notifyHint.innerHTML = "Узнайте ID в <a href=\"https://t.me/userinfobot\" target=\"_blank\" rel=\"noopener\">@userinfobot</a> и нажмите /start у агента магазина.";
    return;
  }
  notifyHint.innerHTML = shop?.has_order_notify
    ? `✅ Уведомления на Telegram ID ${chatId}.`
    : `Сохранён ID ${chatId}. Нажмите <strong>/start</strong> у агента магазина с этого аккаунта.`;
}

function renderTgStatus(connected, username, shopOrNotify) {
  const shop = typeof shopOrNotify === "object" ? shopOrNotify : null;
  const notifyOn = shop ? shop.has_order_notify : !!shopOrNotify;
  const badge       = document.getElementById("tg-status-badge");
  const connBlock   = document.getElementById("tg-connected-block");
  const connectBlock = document.getElementById("tg-connect-block");
  if (connected) {
    badge.textContent = "Подключён";
    badge.className   = "status-badge badge-active";
    connBlock.classList.remove("hidden");
    connectBlock.classList.add("hidden");
    document.getElementById("tg-bot-username").textContent =
      username ? `@${username}` : "Агент активен";
    if (shop) renderTgNotifyHint(shop);
    const testBtn = document.getElementById("tg-test-msg-btn");
    if (testBtn) testBtn.hidden = !(shop && shop.owner_telegram_chat_id);
  } else {
    badge.textContent = "Не подключён";
    badge.className   = "status-badge badge-pending";
    connBlock.classList.add("hidden");
    connectBlock.classList.remove("hidden");
  }
}

function renderMsStatus(connected, apiKey) {
  const badge        = document.getElementById("ms-status-badge");
  const connBlock    = document.getElementById("ms-connected-block");
  const connectBlock = document.getElementById("ms-connect-block");
  if (!badge) return;
  if (connected) {
    badge.textContent = "Подключён";
    badge.className   = "status-badge badge-active";
    connBlock.classList.remove("hidden");
    connectBlock.classList.add("hidden");
    const wh = document.getElementById("ms-webhook-url-connected");
    if (wh) wh.textContent = `${location.origin}/sync/moysklad`;
    const hint = document.getElementById("ms-api-key-hint");
    if (hint) hint.textContent = apiKey || "сгенерируйте ключ в блоке ниже";
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
    const masked = "••••••" + key.slice(-4);
    el.innerHTML = `
      <div class="req-row">
        <span class="req-label">API ключ</span>
        <code class="mono req-value">${esc(masked)}</code>
      </div>
      <p class="muted small">Нажмите "Сгенерировать" чтобы создать новый (старый перестанет работать).</p>
    `;
  } else {
    el.innerHTML = `<p class="muted small">Ключ не сгенерирован. Нажмите кнопку ниже.</p>`;
  }
  // Update 1С setup panel with URL
  const baseUrl = window.location.origin;
  const urlEl = document.getElementById("onec-url");
  const pwdEl = document.getElementById("onec-password");
  if (urlEl) urlEl.textContent = `${baseUrl}/sync/1c-exchange`;
  if (pwdEl) pwdEl.textContent = key ? "••••••" + key.slice(-4) : "— (сгенерируйте ключ выше)";
}

function toggleKeyVisibility(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === "password") { input.type = "text";     btn.textContent = "🙈"; }
  else                           { input.type = "password"; btn.textContent = "👁"; }
}

// ── МойСклад connect ───────────────────────────────────────────────────────────
document.getElementById("ms-connect-btn").addEventListener("click", async () => {
  const token = document.getElementById("ms-token-input").value.trim();
  const errEl = document.getElementById("ms-connect-error");
  errEl.classList.add("hidden");
  if (!token) { errEl.textContent = "Введите токен"; errEl.classList.remove("hidden"); return; }
  try {
    const r = await api("/shop/moysklad-connect", { method: "POST", json: { token } });
    renderMsStatus(true, currentShop?.sync_api_key || null);
    const sample = (r.sample || []).slice(0, 3).join(", ");
    showToast(
      `МойСклад: магазин «${r.shop_name || ""}» — ${r.in_stock || r.imported || 0} в наличии${sample ? ` (${sample})` : ""}`,
      "success",
    );
    document.getElementById("ms-token-input").value = "";
    await loadAll();
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

document.getElementById("ms-sync-btn")?.addEventListener("click", async () => {
  try {
    const r = await api("/shop/moysklad-sync", { method: "POST" });
    const sample = (r.sample || []).slice(0, 3).join(", ");
    showToast(
      `Обновлено: ${r.in_stock || r.imported || 0} в наличии${sample ? ` — ${sample}` : ""}`,
      "success",
    );
    await loadAll();
  } catch (err) {
    showToast(err.message, "error");
  }
});

document.getElementById("ms-replace-btn")?.addEventListener("click", async () => {
  if (!confirm("Удалить все товары не из МойСклад и оставить только каталог МойСклад?")) return;
  try {
    const r = await api("/shop/moysklad-sync?replace=true", { method: "POST" });
    const sample = (r.sample || []).slice(0, 5).join(", ");
    showToast(
      `Каталог заменён: ${r.imported || 0} позиций${sample ? ` (${sample})` : ""}`,
      "success",
    );
    await loadAll();
  } catch (err) {
    showToast(err.message, "error");
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
  if (currentShop?.sync_api_key &&
      !confirm("Сгенерировать новый ключ? Старый перестанет работать.")) return;
  try {
    const r = await api("/shop/sync-api-key", { method: "POST" });
    renderSyncApiKey(r.api_key);
    currentShop = await api("/shop/me");
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

// ── Sidebar plan card ──────────────────────────────────────────────────────────
async function renderSupportFooter() {
  const footer = document.getElementById("dashboard-footer");
  if (!footer) return;
  try {
    const r = await fetch("/api/support");
    if (!r.ok) return;
    const data = await r.json();
    const emailEl = document.getElementById("dashboard-footer-email");
    const tgEl = document.getElementById("dashboard-footer-telegram");
    let any = false;
    if (data.email && emailEl) {
      emailEl.href = `mailto:${data.email}`;
      emailEl.textContent = `✉ ${data.email}`;
      emailEl.hidden = false;
      any = true;
    }
    if (data.telegram && tgEl) {
      const handle = String(data.telegram).replace(/^@/, "");
      tgEl.href = `https://t.me/${handle}`;
      tgEl.textContent = `✈ @${handle}`;
      tgEl.hidden = false;
      any = true;
    }
    footer.hidden = !any;
  } catch {}
}

function renderEmailVerifyBanner(me) {
  const banner = document.getElementById("email-verify-banner");
  if (!banner) return;
  if (!me || me.email_verified) {
    banner.hidden = true;
    return;
  }
  const addr = document.getElementById("email-verify-banner-addr");
  if (addr) addr.textContent = me.owner_email || "ваш адрес";
  banner.hidden = false;
}

document.getElementById("email-verify-resend")?.addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Отправляю…";
  try {
    await api("/shop/resend-verification", { method: "POST" });
    showToast("Письмо отправлено — проверьте почту", "success");
  } catch (err) {
    showToast(err.message || "Ошибка", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

document.getElementById("email-verify-recheck")?.addEventListener("click", async () => {
  try {
    currentShop = await api("/shop/me");
    renderEmailVerifyBanner(currentShop);
    if (currentShop.email_verified) {
      showToast("Email подтверждён ✓", "success");
    } else {
      showToast("Пока не подтверждён — нажмите на ссылку в письме", "info");
    }
  } catch (err) { showToast(err.message, "error"); }
});

function renderTrialCountdown(sub) {
  const el = document.getElementById("trial-countdown");
  if (!el) return;
  const ends = sub && (sub.trial_ends_at || (sub.plan === "trial" && sub.period_ends_at));
  if (!sub || sub.plan !== "trial" || !ends) {
    el.hidden = true;
    return;
  }
  const endTs = new Date(ends).getTime();
  if (isNaN(endTs)) { el.hidden = true; return; }
  const msLeft = endTs - Date.now();
  const daysLeft = Math.ceil(msLeft / 86_400_000);
  let cls = "trial-countdown-ok", label;
  if (daysLeft <= 0) {
    cls = "trial-countdown-danger";
    label = "Trial истёк";
  } else if (daysLeft < 3) {
    cls = "trial-countdown-danger";
    label = `Trial: ${daysLeft} ${pluralDays(daysLeft)} осталось`;
  } else if (daysLeft <= 7) {
    cls = "trial-countdown-warn";
    label = `Trial: ${daysLeft} ${pluralDays(daysLeft)} осталось`;
  } else {
    label = `Trial: ${daysLeft} ${pluralDays(daysLeft)} осталось`;
  }
  el.className = `trial-countdown ${cls}`;
  el.textContent = label;
  el.hidden = false;
  el.onclick = () => document.querySelector('[data-tab=subscription]')?.click();
  el.style.cursor = "pointer";
}

function pluralDays(n) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "день";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "дня";
  return "дней";
}

function updateSidebarPlan(sub, shopName) {
  const nameEl = document.getElementById("sidebar-plan-name");
  const infoEl = document.getElementById("sidebar-plan-info");
  const fillEl = document.getElementById("sidebar-plan-fill");
  if (!nameEl) return;

  const plan = sub ? (sub.plan || "trial").toUpperCase() : "TRIAL";
  nameEl.textContent = `Тариф ${plan}`;
  const used      = sub ? (sub.messages_used  ?? 0) : 0;
  const limit     = sub ? (sub.messages_limit ?? 0) : 0;
  const unlimited = sub ? (sub.unlimited || limit >= 999999) : false;
  if (infoEl) infoEl.textContent = unlimited ? `${used} сообщений (безлимит)` : `${used} / ${limit} сообщений`;
  if (fillEl) fillEl.style.width = unlimited ? "40%" : (limit > 0 ? Math.min(100, Math.round(used / limit * 100)) + "%" : "0%");

  renderTrialCountdown(sub);

  const avatarEl = document.getElementById("topbar-avatar");
  if (avatarEl && shopName) {
    const words = shopName.trim().split(/\s+/);
    avatarEl.textContent = words.length >= 2
      ? (words[0][0] + words[1][0]).toUpperCase()
      : shopName.slice(0, 2).toUpperCase();
  }
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
        <div class="sub-detail"><div class="label">Сообщения</div><div class="value">${sub.unlimited || sub.messages_limit >= 999999 ? `${sub.messages_used || 0} / ∞` : `${sub.messages_used || 0} / ${sub.messages_limit}`}</div></div>
        <div class="sub-detail"><div class="label">Каналов</div><div class="value">${sub.channels_limit}</div></div>
        ${ends ? `<div class="sub-detail"><div class="label">Действует до</div><div class="value" style="font-size:15px">${new Date(ends).toLocaleDateString("ru-RU")}</div></div>` : ""}
      </div>
      <div class="payment-info">
        <h3>Продление тарифа</h3>
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
  const rows = (result.preview || []).map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.sku || "—")}</td><td>${esc(p.category || "—")}</td><td>${esc(p.quantity)}</td><td>${fmtPrice(p.price)}</td></tr>`).join("");
  el.innerHTML = `<p><strong>Файл валиден:</strong> ${result.valid_rows} строк</p>
    <table class="preview-table"><thead><tr><th>Название</th><th>SKU</th><th>Категория</th><th>Остаток</th><th>Цена</th></tr></thead><tbody>${rows}</tbody></table>`;
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
let _lastAnalytics = null;

function destroyChart(id) {
  if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
}

function renderAnalytics(data) {
  if (!data || data.error) return;
  _lastAnalytics = data;

  // 1. Messages per day — line chart (accent gradient, clean axes)
  destroyChart("messages");
  const msgCtx = document.getElementById("chart-messages");
  if (msgCtx) {
    const labels = data.daily_messages.map(r => r.day.slice(5)); // MM-DD
    const values = data.daily_messages.map(r => r.cnt);
    const g = msgCtx.getContext("2d").createLinearGradient(0, 0, 0, 240);
    g.addColorStop(0, "rgba(59,130,246,0.20)");
    g.addColorStop(1, "rgba(59,130,246,0)");
    _charts["messages"] = new Chart(msgCtx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Сообщений", data: values,
          borderColor: "#3B82F6", borderWidth: 3,
          backgroundColor: g, tension: 0.4, fill: true,
          pointRadius: 0, pointHoverRadius: 6,
          pointBackgroundColor: "#fff", pointBorderColor: "#3B82F6", pointBorderWidth: 2.5,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: { legend: { display: false }, tooltip: TOOLTIP_STYLE },
        scales: {
          x: { grid: { display: false }, border: { display: false }, ticks: { color: chartTextColor(), maxTicksLimit: 8 } },
          y: { beginAtZero: true, ticks: { precision: 0, color: chartTextColor(), maxTicksLimit: 5 },
               grid: { color: chartGridColor() }, border: { display: false } },
        },
      },
    });
  }

  // 2. Top categories — horizontal bar chart (accent tones)
  destroyChart("brands");
  const brandCtx = document.getElementById("chart-brands");
  const topCats = data.top_categories || data.top_brands || [];
  if (brandCtx && topCats.length) {
    _charts["brands"] = new Chart(brandCtx, {
      type: "bar",
      data: {
        labels: topCats.map(r => r.category || r.brand),
        datasets: [{
          label: "Позиций в наличии",
          data: topCats.map(r => r.cnt),
          backgroundColor: ["#3B82F6","#6366f1","#10b981","#f97316","#e11d48","#0891b2","#8b5cf6","#64748b"],
          borderRadius: 7, barThickness: "flex", maxBarThickness: 26,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: TOOLTIP_STYLE },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0, color: chartTextColor() }, grid: { color: chartGridColor() }, border: { display: false } },
          y: { ticks: { color: chartTextColor() }, grid: { display: false }, border: { display: false } },
        },
      },
    });
  }

  // 3. Orders by status — doughnut chart (accent palette)
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
            backgroundColor: ["#3B82F6","#10b981","#6366f1","#e11d48"],
            borderWidth: 3, borderColor: document.body.classList.contains("sb-dark") ? "#1c1c20" : "#ffffff",
            hoverOffset: 6,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: {
            legend: { position: "bottom", labels: { font: { size: 12 }, color: chartTextColor(), padding: 14, usePointStyle: true, pointStyle: "circle" } },
            tooltip: TOOLTIP_STYLE,
          },
          cutout: "68%",
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

// ── Insights ───────────────────────────────────────────────────────────────────
function renderInsights(data) {
  if (!data || data.error) return;

  const pctEl  = document.getElementById("conv-pct");
  const metaEl = document.getElementById("conv-meta");
  if (pctEl) {
    const pct = data.conversion_pct ?? 0;
    pctEl.textContent = pct + "%";
    pctEl.style.color = pct >= 5 ? "var(--success-text)" : pct >= 2 ? "#d97706" : "#dc2626";
  }
  if (metaEl) {
    const lat = data.avg_latency_ms ? ` · ${data.avg_latency_ms} мс` : "";
    metaEl.textContent = `${data.orders_from_bot} заказов из ${data.total_messages} сообщений${lat}`;
  }

  const missedEl = document.getElementById("missed-queries-list");
  if (missedEl) {
    const rows = data.top_missed_queries || [];
    if (!rows.length) {
      missedEl.innerHTML = `<div style="padding:32px;text-align:center;color:var(--text3)">Пустых запросов нет 🎉</div>`;
      return;
    }
    const max = rows[0].cnt || 1;
    missedEl.innerHTML = rows.map(r => {
      const pct = Math.round(r.cnt / max * 100);
      const q = r.query.length > 38 ? r.query.slice(0,38) + "…" : r.query;
      return `
        <div class="intent-header">
          <span class="intent-name">${esc(q)}</span>
          <span class="intent-cnt">${r.cnt}×</span>
        </div>
        <div class="a-bar-track" style="margin-top:5px"><div class="a-bar-fill" style="width:${pct}%;background:#f97316"></div></div>
      `;
    }).join("");
  }
}

// ── Analytics KPIs & category bars ────────────────────────────────────────────
function renderAnalyticsKPIs(insights, stats) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("kpi-messages", (stats?.messages ?? 0).toLocaleString("ru-RU"));
  if (insights) {
    const pct = insights.conversion_pct ?? 0;
    set("kpi-conv", pct + "%");
    const tEl = document.getElementById("kpi-conv-trend");
    if (tEl) tEl.textContent = pct >= 5 ? "↑ хорошо" : pct >= 2 ? "→ средне" : "↓ низко";
    set("kpi-orders-cnt", (insights.orders_from_bot ?? 0).toLocaleString("ru-RU"));
    const lat = insights.avg_latency_ms;
    set("kpi-latency", lat ? (lat >= 1000 ? (lat/1000).toFixed(1)+"с" : lat+"мс") : "—");
  }
}

function renderAnalyticsCatBars(analyticsData) {
  const el = document.getElementById("analytics-cat-bars");
  if (!el) return;
  const cats = analyticsData?.top_categories || analyticsData?.top_brands || [];
  if (!cats.length) { el.innerHTML = `<p class="muted center" style="padding:32px 0">Нет данных</p>`; return; }
  const max = cats[0].cnt || 1;
  el.innerHTML = cats.slice(0,5).map(r => {
    const pct = Math.round(r.cnt / max * 100);
    return `
      <div class="a-bar-row">
        <span class="a-bar-label" title="${esc(r.category||r.brand)}">${esc(r.category||r.brand)}</span>
        <div class="a-bar-track"><div class="a-bar-fill" style="width:${pct}%"></div></div>
        <span class="a-bar-pct">${pct}%</span>
      </div>
    `;
  }).join("");
}

// ── Agent card view ────────────────────────────────────────────────────────────
function showAgentCard(me) {
  const nameEl   = document.getElementById("agent-display-name");
  const descEl   = document.getElementById("agent-display-desc");
  const statusEl = document.getElementById("agent-display-status");
  if (nameEl) nameEl.textContent = me.name || "Агент";
  if (descEl) descEl.textContent = me.bot_role || "Консультирует клиентов";
  if (statusEl) {
    const on = me.has_tg_bot;
    statusEl.className = "agent-pill " + (on ? "on" : "off");
    statusEl.innerHTML = on
      ? `<span class="pulse-dot"></span>&nbsp;Активен`
      : `<span class="pulse-dot" style="animation:none"></span>&nbsp;Не подключён`;
  }
}

function updateAgentMetrics(stats, insights) {
  const s = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  s("agent-m-dialogs",  (stats?.conversations??0).toLocaleString("ru-RU"));
  s("agent-m-messages", (stats?.messages??0).toLocaleString("ru-RU"));
  s("agent-m-orders",   (stats?.orders??0).toLocaleString("ru-RU"));
  if (insights) s("agent-m-conv", (insights.conversion_pct??0) + "%");
}

// ── Load all ───────────────────────────────────────────────────────────────────
async function loadAll() {
  try {
    const [me, stats, products, orders, messages, sub, payInfo, analytics, insights] = await Promise.all([
      api("/shop/me"),
      api("/shop/stats"),
      api(`/shop/products?limit=${catalogLimit}&offset=${catalogOffset}`),
      api("/shop/orders?limit=100"),
      api("/shop/messages?limit=40"),
      api("/shop/subscription").catch(() => null),
      api("/shop/payment-info").catch(() => null),
      api("/shop/analytics/overview").catch(() => null),
      api("/shop/analytics/insights").catch(() => null),
    ]);
    currentShop = me;
    document.getElementById("shop-name-sidebar").textContent = me.name ? `${me.name} #${me.id}` : "Магазин";
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
    renderEmailVerifyBanner(me);
    renderSetupChecklist(me, stats);
    renderSupportFooter();
    // Overview extras
    renderOverviewMiniChart(analytics);
    renderOverviewAgent(me, stats);
    renderOverviewConversations(messages.items || []);
    renderOverviewIntegrations(me);
    // Full messages list
    renderMessages(messages.items || [], "messages-list");
    renderCatalog(products);
    renderOrders(orders);
    fillBotSettings(me);
    showAgentCard(me);
    updateAgentMetrics(stats, insights);
    renderSubscription(sub);
    updateSidebarPlan(sub, me.name);
    renderPaymentInfo(payInfo);
    renderProfile(me);
    renderAnalytics(analytics);
    renderInsights(insights);
    renderAnalyticsKPIs(insights, stats);
    renderAnalyticsCatBars(analytics);
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
// After a successful import refresh ONLY the catalog (the import re-embeds 500 rows
// synchronously on the server, so loadAll()'s ~10 parallel calls just pile on). Reset
// to the first page so the freshly imported rows are visible.
async function refreshCatalog() {
  catalogOffset = 0;
  renderCatalog(await api(`/shop/products?limit=${catalogLimit}&offset=0`));
}

// Disable the button + show progress while the request runs. The server now embeds
// the catalog on import (seconds for 500 rows); without this the owner could click
// twice → a double import + double embedding pass. Pattern mirrors email-verify-resend.
async function runImport(btn, path, okMsg) {
  if (btn.disabled) return;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = "Импортирую… считаю эмбеддинги…";
  try {
    const r = await uploadCsv(path);
    if (r) { showToast(okMsg(r), "success"); await refreshCatalog(); }
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

document.getElementById("import-btn").addEventListener("click", (e) =>
  runImport(e.currentTarget, "/shop/import", (r) => `Импортировано: ${r.imported}`)
);
document.getElementById("replace-btn").addEventListener("click", (e) => {
  if (!confirm("Заменить весь каталог? Старые товары будут удалены.")) return;
  runImport(e.currentTarget, "/shop/import?replace=true", (r) => `Каталог заменён: ${r.imported} позиций`);
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
    showToast("Сначала сгенерируйте Sync API Key в разделе Агент", "error");
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
    const groqKey = document.getElementById("groq-key-input")?.value.trim() || "";
    await patchApi("/shop/settings", {
      name: document.getElementById("shop-name-input").value.trim() || null,
      bot_role: document.getElementById("bot-role-input").value.trim() || null,
      business_type: document.getElementById("business-type-input").value || null,
      website_url: document.getElementById("website-url-input").value.trim() || null,
      groq_system_prompt: document.getElementById("bot-prompt-input").value.trim() || null,
      groq_api_key: groqKey || null,
    });
    showToast("Настройки сохранены", "success");
    currentShop = await api("/shop/me");
    document.getElementById("shop-name-sidebar").textContent = currentShop.name;
    fillBotSettings(currentShop);
  } catch (err) { showToast(err.message, "error"); }
});

document.getElementById("clear-groq-key-btn")?.addEventListener("click", async () => {
  if (!confirm("Удалить свой Groq API ключ? Агент будет использовать ключ платформы.")) return;
  try {
    await patchApi("/shop/settings", { clear_groq_api_key: true });
    showToast("Свой Groq ключ удалён", "success");
    currentShop = await api("/shop/me");
    fillBotSettings(currentShop);
  } catch (err) { showToast(err.message, "error"); }
});


// ── Sandbox chat (test the bot against the catalog) ────────────────────────────
const sandboxState = { history: [], pending: false };

function sandboxRenderHistory() {
  const log = document.getElementById("sandbox-log");
  if (!log) return;
  if (sandboxState.history.length === 0) {
    log.innerHTML = '<div class="sandbox-empty" id="sandbox-empty">Введите сообщение, чтобы начать.</div>';
    return;
  }
  log.innerHTML = sandboxState.history.map(m => {
    const isUser = m.role === "user";
    const cls = isUser ? "sandbox-msg sandbox-msg-user" : "sandbox-msg sandbox-msg-assistant";
    const label = isUser ? "Вы" : "Агент";
    return `<div class="${cls}">
      <div class="sandbox-msg-label">${label}</div>
      <div>${esc(m.content)}</div>
    </div>`;
  }).join("");
  log.scrollTop = log.scrollHeight;
}

function sandboxRenderProducts(products) {
  const wrap = document.getElementById("sandbox-products");
  const list = document.getElementById("sandbox-products-list");
  if (!wrap || !list) return;
  if (!products || products.length === 0) {
    wrap.hidden = true;
    list.textContent = "";
    return;
  }
  wrap.hidden = false;
  list.textContent = products.map(p => p.name).filter(Boolean).join(", ");
}

async function sandboxSend() {
  if (sandboxState.pending) return;
  const input = document.getElementById("sandbox-input");
  const sendBtn = document.getElementById("sandbox-send");
  const message = (input?.value || "").trim();
  if (!message) return;
  sandboxState.history.push({ role: "user", content: message });
  sandboxRenderHistory();
  input.value = "";
  sandboxState.pending = true;
  sendBtn.disabled = true;
  sendBtn.textContent = "Думаю…";
  try {
    const data = await api("/shop/sandbox-chat", {
      method: "POST",
      json: { message, history: sandboxState.history.slice(0, -1) },
    });
    sandboxState.history.push({ role: "assistant", content: data.reply || "" });
    sandboxRenderHistory();
    sandboxRenderProducts(data.products || []);
  } catch (err) {
    showToast(err.message || "Ошибка", "error");
    sandboxState.history.pop();
    sandboxRenderHistory();
  } finally {
    sandboxState.pending = false;
    sendBtn.disabled = false;
    sendBtn.textContent = "Отправить";
    input.focus();
  }
}

document.getElementById("sandbox-send")?.addEventListener("click", sandboxSend);
document.getElementById("sandbox-input")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sandboxSend();
  }
});
document.getElementById("sandbox-reset")?.addEventListener("click", () => {
  sandboxState.history = [];
  sandboxRenderHistory();
  sandboxRenderProducts([]);
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
document.getElementById("save-notify-settings")?.addEventListener("click", async () => {
  const raw = document.getElementById("owner-tg-id-input")?.value.trim() || "";
  try {
    const res = await patchApi("/shop/settings", { owner_telegram_chat_id: raw || null });
    showToast(
      res.has_order_notify ? "Telegram ID сохранён" : "Сохранено — нажмите /start у агента магазина",
      "success",
    );
    currentShop = await api("/shop/me");
    fillBotSettings(currentShop);
  } catch (err) { showToast(err.message, "error"); }
});

document.getElementById("tg-connect-btn").addEventListener("click", async () => {
  const token  = document.getElementById("tg-token-input").value.trim();
  const errEl  = document.getElementById("tg-connect-error");
  errEl.classList.add("hidden");
  if (!token) { errEl.textContent = "Вставьте токен"; errEl.classList.remove("hidden"); return; }
  try {
    const r = await api("/shop/bot-connect", { method: "POST", json: { tg_token: token } });
    document.getElementById("tg-token-input").value = "";
    currentShop = await api("/shop/me");
    renderTgStatus(true, r.bot_username, currentShop);
    showToast(`Агент @${r.bot_username || "?"} подключён!`, "success");
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove("hidden");
  }
});

document.getElementById("tg-disconnect-btn").addEventListener("click", async () => {
  if (!confirm("Отключить Telegram агента? Агент перестанет отвечать клиентам.")) return;
  try {
    await api("/shop/bot-connect", { method: "DELETE" });
    currentShop = await api("/shop/me");
    renderTgStatus(false, null, currentShop);
    showToast("Агент отключён", "info");
  } catch (err) { showToast(err.message, "error"); }
});

document.getElementById("tg-test-msg-btn")?.addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Отправляю…";
  try {
    await api("/shop/bot-test-message", { method: "POST" });
    showToast("Тест отправлен — проверьте Telegram", "success");
  } catch (err) {
    showToast(err.message || "Ошибка", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
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
  const termsOk  = document.getElementById("reg-terms")?.checked;
  const errEl    = document.getElementById("register-error");
  const okEl     = document.getElementById("register-success");
  errEl.classList.add("hidden");
  okEl.classList.add("hidden");

  if (!termsOk) {
    errEl.textContent = "Примите оферту и политику конфиденциальности";
    errEl.classList.remove("hidden");
    return;
  }

  try {
    const res = await fetch("/shop/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shop_name: name, email, password, accepted_terms: true }),
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

// ── Re-theme charts when dark mode toggles ──────────────────────────────────────
(function watchTheme() {
  let last = document.body.classList.contains("sb-dark");
  new MutationObserver(() => {
    const now = document.body.classList.contains("sb-dark");
    if (now !== last) {
      last = now;
      if (_lastAnalytics) { renderAnalytics(_lastAnalytics); renderOverviewMiniChart(_lastAnalytics); }
    }
  }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
})();

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
