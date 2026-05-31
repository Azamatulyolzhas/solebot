# SoleBot — Architecture

## Entry point
- `main.py` — FastAPI app, lifespan (DB init, bot setup), CORS, router includes

## Configuration
- `config.py` — all env variables
- `.env` / `.env.example` — secrets (not committed)
- `models.py` — Pydantic request/response schemas

## Database
- `db.py` — connection factory, `fetch_all`, `fetch_one_value`, helpers
- `schema.py` — `ensure_app_tables()` — DDL for all tables
- `sneakers.db` — SQLite dev DB (ignored by git in production)

## Core modules
| File | Responsibility |
|---|---|
| `products.py` | Catalog CRUD, CSV import/export, **RAG retrieval** (`build_product_context`) |
| `ai.py` | Groq API call, RAG integration, rate limiting, conversation history |
| `conversations.py` | Save/load messages, analytics events |
| `orders.py` | Order creation, order flow state machine |
| `shops.py` | Multi-shop support, resolve/list shops |
| `billing.py` | Subscription checks |
| `notifications.py` | Telegram manager notifications |

## Channels
| File | Channel |
|---|---|
| `telegram_bot.py` | Telegram (default bot + per-shop bots) |
| `whatsapp_client.py` | WhatsApp Business API |
| `instagram_client.py` | Instagram Messenger API |

## API routes
- `routes/api.py` — public endpoints: `GET /`, `POST /api/chat`, `/tg/*`, `/wa/*`, `/ig/*`
- `routes/admin.py` — admin endpoints: `/admin/*` (stats, products, orders, CSV, messages)

## Admin frontend (static SPA)
- `admin/index.html` — shell page
- `admin/styles.css` — styles
- `admin/app.js` — client-side logic (login, tabs, API calls)

## Infrastructure
- `cache.py` — Redis sessions, rate limiting, in-memory fallback
- `admin_service.py` — DB stats helpers, auth guard, `html_escape`
- `migrate_sqlite_to_postgres.py` — one-off migration script

## RAG strategy
1. `normalize_query()` — expand aliases (af1 → air force 1, etc.)
2. Browse query → grouped catalog summary (≤60 models, token-capped)
3. Specific query → two-stage: find `(brand, model)` pairs → fetch SKUs
4. Always prepend brand list so LLM knows full inventory
5. Context hard-capped at 3000 chars (~750 tokens)
