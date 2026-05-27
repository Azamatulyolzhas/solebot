# SoleBot Architecture

Current direction:

- `main.py` keeps the FastAPI app and existing routes during the transition.
- `config.py` owns environment variables.
- `billing.py` owns subscription checks.
- `models.py` owns small request/response schemas.

Next extraction steps:

1. `db.py` - database, catalog, orders, analytics, CSV import/export.
2. `cache.py` - Redis sessions, order state, rate limiting.
3. `ai.py` - RAG prompt, Groq call, fallback, order flow.
4. `telegram_channel.py` - Telegram bots and webhook setup.
5. `admin.py` - `/admin/*` routes and HTML rendering.
