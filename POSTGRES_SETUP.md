# SoleBot PostgreSQL Setup

## What Changed

The app now supports two database modes:

- SQLite: default local mode, uses `sneakers.db`.
- PostgreSQL: production mode, enabled when `DATABASE_URL` is set.

This lets you keep developing locally while Railway runs on managed PostgreSQL.

## Railway Steps

1. Add a PostgreSQL service to the Railway project.
2. Connect the Postgres variable to the bot service:

```env
DATABASE_URL=${{ Postgres.DATABASE_URL }}
```

3. Deploy the bot so `psycopg[binary]` is installed from `requirements.txt`.
4. Run the migration once from a machine that has access to the same `DATABASE_URL`:

```bash
python migrate_sqlite_to_postgres.py
```

The script creates these tables:

- `shops`
- `sneakers`
- `orders`
- `conversations`
- `messages`
- `analytics_events`

It imports the current SQLite catalog into a default shop.

## Local Development

If `DATABASE_URL` is empty, the app keeps using:

```env
DB_PATH=sneakers.db
```

Healthcheck shows the active database:

```json
{
  "database": "sqlite"
}
```

or:

```json
{
  "database": "postgresql"
}
```
