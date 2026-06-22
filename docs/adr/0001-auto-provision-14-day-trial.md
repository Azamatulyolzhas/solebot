# ADR-0001: Auto-provision 14-day trial on shop registration (drop manual approval gate)

- **Status:** Accepted
- **Date:** 2026-06-22
- **Deciders:** Azamatulyolzhas (solo founder)
- **Supersedes:** —
- **Superseded by:** —
- **Amends:** —
- **Depends on:** —

---

## Context

Vendly is a multi-tenant FastAPI + Groq AI Telegram-bot SaaS targeting SMB shops in KZ/RU. The project is pre-launch with zero paying customers and a solo founder.

The previous registration flow required a manual admin approval step:

1. Shop owner calls `POST /shop/register` → row created with `status='pending'`
2. Owner receives email "we'll review within 24 hours"
3. Login blocked until admin clicks "Approve" in the admin panel ("Заявки" tab)
4. Admin click creates a 30-day trial subscription

This manual gate was identified as the #1 activation killer in the pre-launch sprint (sprint goal: 70% activation improvement). The 24-hour delay between signup and dashboard access is fatal for this product because the "aha moment" — uploading a product catalog and connecting a Telegram bot — is gated behind login. A cold visitor who signs up and then waits a day does not return.

---

## Decision

Replace the pending-and-approve flow with immediate auto-provisioning:

1. `POST /shop/register` now creates the shop row with `status='active'` immediately.
2. A trial subscription is created **atomically in the same DB transaction** as the shop row:
   - `plan = 'trial'`
   - `messages_limit = 500`
   - `channels_limit = 1`
   - `trial_ends_at = NOW() + 14 days`
3. The endpoint returns a **JWT in the response body** so the frontend can log the user in immediately — no separate `/login` call required.
4. A single **welcome email** replaces the old two-email sequence ("received" + "approved").
5. The admin `PATCH /admin/shops/{id}/status` endpoint is **retained** as a postfactum suspend/reject escape hatch. Only the "Заявки" UI tab is hidden (`display: none`) — the underlying code is not deleted.
6. Per-IP rate limit on `/register`: 5 registrations per 600-second window (mirrors the existing login rate limit).
7. `shop_name` is HTML-escaped in the welcome email template.
8. Shop slug generation switches from `int(time.time()) % 100000` to `secrets.token_hex(4)` (8 hex chars) to eliminate same-second collision risk.

---

## Trade-offs and Known Gaps

### Email enumeration via HTTP 409 on duplicate email
`POST /register` returns HTTP 409 when the email is already registered. This leaks whether an email exists. The auto-login UX requires distinguishing a successful registration (returns a JWT) from a duplicate (returns an error), so the leak is inherent to the design. **Mitigation:** the 5/600s rate limit makes brute enumeration impractical (~33 minutes per 100 addresses). This is the same trade-off accepted by Linear, Notion, and most SaaS products. Revisit when email verification is added.

### No email verification before trial issuance
A user can register under someone else's email address and receive a valid JWT. This is a pre-existing gap not introduced by this ADR. **Mitigation deferred** to post-launch hardening: add a verify-email gate before bot-connect and CSV-import actions.

### Trial farm risk
Even with the rate limit, a motivated attacker can create N trial accounts to consume free Groq quota. **Mitigation deferred:** monitor Groq spend per-day (covered by sprint Priority 4 — Groq cost visibility). Revisit with phone/card verification if abuse is observed.

### Double-gate bug exposure (not regressed)
`is_subscription_active()` is called twice per message flow — once in `telegram_bot.py` and once in `ai.py` (lines 520/526). Every new auto-provisioned shop has an active trial, so fresh signups will exercise this code path immediately rather than after a 24h delay. This **does not make the bug worse** but does make it visible sooner. Fix deferred per sprint priorities.

---

## Consequences

- **Activation friction:** reduced from ~24 hours to 0 seconds.
- **Admin "Заявки" workflow:** dead for new registrations; its code is retained. Reverting is a single CSS unhide + one JS line.
- **Admin panel `/admin/shops`:** new trial shops appear with `status=active, plan=trial`. Admins can still suspend via the existing endpoint.
- **Atomicity:** shop creation and trial subscription creation succeed or fail together; no orphan shop rows without a trial.

---

## Files Affected

| File | Change |
|---|---|
| `shops.py` | Added `create_active_shop_with_trial()`, constant `TRIAL_DAYS_DEFAULT = 14`; removed `create_pending_shop()` |
| `email_service.py` | Added `send_shop_welcome()`; removed `send_shop_registered()` |
| `routes/shop.py` | `shop_register` handler rewritten; `_check_register_rate()` added |
| `admin/index.html` | "Заявки" tab hidden via `display: none` |
| `admin/app.js` | `loadApplications()` removed from initial page load |

---

## References

- Pre-launch sprint plan — activation improvements (70% goal)
- [Groq cost visibility task — sprint Priority 4]
- Standard SaaS enumeration trade-off: Linear signup flow, Notion signup flow
