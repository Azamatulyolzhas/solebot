"""Pytest bootstrap.

Sets a deterministic environment BEFORE any project module (which imports
``config`` and calls ``load_dotenv``) is imported, so tests never touch a real
Postgres DB or depend on a developer's ``.env``.

``load_dotenv`` does not override variables already present in ``os.environ``,
so assigning here wins over whatever ``.env`` contains.
"""
import os

# Force SQLite code paths and keep tests off any production database.
os.environ["DATABASE_URL"] = ""
# Deterministic JWT secret so token round-trip tests are stable.
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789abcdef")
# Admin credentials used by auth tests.
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
