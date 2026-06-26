"""diag.py — one-off diagnostics for the two live bugs:

  BUG 3  order captured a phantom/stale product ("…, Nike Air Force 1 Low x2")
  BUG 2  selection phrases ("нью баланс", "1 вариант") dead-end into "не нашёл"

It reads the SAME database/Redis the bot uses (config.py loads .env on import),
so the evidence is the real production data, not a guess.

Usage (from the project root, inside your venv):

    python diag.py --user 945221727 --shop vardly
    python diag.py --user 945221727 --shop-id 1 --probe        # also hit the LLM search
    python diag.py --shop vardly --dups                        # only the catalog-dup check

Flags:
    --user      external_user_id (the telegram id, e.g. 945221727)
    --shop      shop name substring (case-insensitive) to resolve the shop id
    --shop-id   shop id directly (skips name lookup)
    --probe     run the live search for the selection phrases (consumes Groq tokens)
    --dups      run only the duplicate-catalog-rows check

Nothing here writes to the DB or Redis — it is read-only.
"""

import argparse
import asyncio
import re
import sys

# The Windows console defaults to cp1251 here, which can't encode '→'/Cyrillic.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# config.py calls load_dotenv() at import time, so importing any project module
# populates DATABASE_URL / REDIS_URL / GROQ_* from .env.
from config import REDIS_URL, USE_POSTGRES
from db import db_placeholder, fetch_all


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _norm(name: str) -> str:
    """The normalization _interest_names SHOULD do but doesn't: casefold + collapse
    whitespace. Two raw names that collapse to the same norm are dedup-defeating."""
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


# ── shop resolution ──────────────────────────────────────────────────────────
def resolve_shop(shop_name: str | None, shop_id: int | None) -> int | None:
    if shop_id is not None:
        return shop_id
    if not shop_name:
        return None
    ph = db_placeholder()
    rows = fetch_all(
        f"SELECT id, name FROM shops WHERE lower(name) LIKE lower({ph})",
        (f"%{shop_name}%",),
    )
    if not rows:
        print(f"!! no shop matched name ~ {shop_name!r}")
        return None
    for r in rows:
        print(f"   shop id={r['id']}  name={r['name']!r}")
    if len(rows) > 1:
        print("!! multiple shops matched — re-run with --shop-id <id>")
        return None
    return rows[0]["id"]


# ── CHECK 1: orders table (durable evidence for BUG 3) ───────────────────────
def check_orders(external_user_id: str) -> None:
    _hr(f"CHECK 1 — orders for external_user_id={external_user_id} (oldest → newest)")
    ph = db_placeholder()
    rows = fetch_all(
        f"""
        SELECT id, created_at, channel, status, product_interest
        FROM orders
        WHERE external_user_id = {ph}
        ORDER BY id
        """,
        (external_user_id,),
    )
    if not rows:
        print("   (no orders for this user)")
        return
    for r in rows:
        pi = r.get("product_interest") or ""
        names = [n.strip() for n in pi.split(",") if n.strip()]
        dup = len(names) != len(set(names))
        flag = "  <-- DUPLICATE NAMES" if dup else ""
        print(f"   #{r['id']} [{r['created_at']}] {r['channel']}/{r['status']}")
        print(f"        Товар: {pi!r}{flag}")
    print(
        "\n   Reading: if an EARLIER order already names a product that the LATER\n"
        "   order shouldn't have, the order pipeline pulled STALE cross-session\n"
        "   interest. Duplicate names in one row = exact-name dedup was defeated."
    )


# ── CHECK 2: duplicate / dedup-defeating catalog rows (BUG 3 doubling) ────────
def check_dups(shop_id: int | None) -> None:
    _hr("CHECK 2 — catalog rows whose name defeats exact-string dedup")
    from products import get_all_catalog_products

    products = get_all_catalog_products(shop_id)
    print(f"   catalog rows: {len(products)}")

    groups: dict[str, set] = {}
    for p in products:
        groups.setdefault(_norm(p.get("name") or ""), set()).add(p.get("name") or "")
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if dups:
        print("   !! same product, MORE THAN ONE raw spelling (this makes 'X, X'):")
        for norm, variants in dups.items():
            print(f"      norm={norm!r}")
            for v in sorted(variants):
                print(f"        raw={v!r} (len {len(v)})")
    else:
        print("   ok — every product name has a single raw spelling (no dedup-defeat)")

    # Show the brands from the transcripts explicitly, with repr to expose spaces.
    _hr("CHECK 2b — rows for the brands seen in the transcripts")
    for needle in ("air force", "balance", "ultraboost"):
        hits = [p for p in products if needle in (p.get("name") or "").lower()]
        print(f"\n   '{needle}': {len(hits)} row(s)")
        for p in hits:
            print(
                f"        id={p.get('id')} sku={p.get('sku')!r} "
                f"name={p.get('name')!r} qty={p.get('quantity')}"
            )


# ── CHECK 3: Redis state (only useful if Redis is configured) ────────────────
async def check_redis(external_user_id: str) -> None:
    _hr("CHECK 3 — Redis conversation state")
    if not REDIS_URL:
        print(
            "   REDIS_URL not set → state lives in the in-memory dicts in cache.py.\n"
            "   It cannot be inspected from outside the running process, and any\n"
            "   restart/redeploy already wiped it. (This is why a stale value can\n"
            "   also survive only until the next process restart, not via Redis.)"
        )
        return
    try:
        import redis.asyncio as redis
    except ImportError:
        print("   redis package not installed; skipping")
        return
    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        found = False
        for prefix in ("interest", "shown", "order", "session", "activity"):
            async for key in client.scan_iter(match=f"{prefix}:*{external_user_id}*"):
                found = True
                val = await client.get(key)
                ttl = await client.ttl(key)
                print(f"   {key}  (ttl={ttl}s)\n        {val!r}")
        if not found:
            print(f"   no keys matched *{external_user_id}* (likely expired/cleared)")
    finally:
        await client.aclose()


# ── CHECK 4: live search probe (reproduces BUG 2 deterministically) ──────────
PROBE_QUERIES = [
    "new balance",                    # positive control (Latin) — should hit
    "кроссовки для бега",             # positive control (semantic)
    "нью баланс",                     # transcript: Cyrillic brand → ?
    "Тогда давай куплю нью баланс",   # transcript: selection phrase → "не нашёл"
    "адидас",                         # earlier transcript: Cyrillic brand
    "давай тогда адидас",             # earlier transcript: selection phrase
    "1 вариант",                      # transcript: ordinal selection → "не нашёл"
]


async def check_probe(shop_id: int | None, last_interest: str) -> None:
    _hr("CHECK 4 — live search probe (why each selection phrase dead-ends)")
    from ai import is_affirmation, is_followup_question
    from billing import resolve_groq_api_key
    from products import (
        extract_query_words,
        get_relevant_products,
        is_browse_query,
        search_products_db,
        vector_search_products,
    )
    from shops import get_shop_by_id, resolve_shop_id

    sid = resolve_shop_id(shop_id)
    shop = get_shop_by_id(sid) or {}
    api_key = resolve_groq_api_key(sid)
    print(f"   shop_id={sid}  api_key={'set' if api_key else 'MISSING'}")
    print(f"   simulated last_interest={last_interest!r}\n")
    print(
        "   columns: AFF=is_affirmation FUP=is_followup_question BRW=is_browse_query\n"
        "            vec=pgvector hits  kw=keyword-backstop hits  total=final hits\n"
    )
    for q in PROBE_QUERIES:
        aff = is_affirmation(q)
        fup = is_followup_question(q, last_interest)
        brw = is_browse_query(q)
        vec = len(vector_search_products(q, sid, limit=20))
        words = extract_query_words(q)
        kw = len(search_products_db(words, sid, limit=20)) if words else 0
        total = await get_relevant_products(q, sid, shop=shop, api_key=api_key, history=[])
        names = ", ".join(p.get("name") or "" for p in total[:4])
        verdict = "DEAD-END" if not total and not (aff or fup) else ("ok" if total else "—")
        print(f"   {q!r}")
        print(
            f"        AFF={aff!s:<5} FUP={fup!s:<5} BRW={brw!s:<5} "
            f"vec={vec} kw={kw} total={len(total)}  [{verdict}]"
        )
        if names:
            print(f"        -> {names}")
    print(
        "\n   Reading: a row with AFF=False FUP=False total=0 is the bug — the phrase\n"
        "   references the just-shown set, but routing sends it to a fresh search\n"
        "   that returns nothing. vec=0 kw=0 on a Cyrillic brand confirms the miss\n"
        "   is cross-script, not just a flaky LLM call."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnostics for the order/search bugs")
    ap.add_argument("--user", help="external_user_id (telegram id)")
    ap.add_argument("--shop", help="shop name substring")
    ap.add_argument("--shop-id", type=int, help="shop id (skips name lookup)")
    ap.add_argument("--probe", action="store_true", help="run the live LLM search probe")
    ap.add_argument("--dups", action="store_true", help="only the catalog-dup check")
    ap.add_argument(
        "--last-interest",
        default="Adidas Ultraboost 22 Black, New Balance 990v5",
        help="simulated running interest for the probe's follow-up check",
    )
    args = ap.parse_args()

    print(f"backend: {'Postgres' if USE_POSTGRES else 'SQLite'} | "
          f"Redis: {'on' if REDIS_URL else 'off (in-memory)'}")

    _hr("shop resolution")
    shop_id = resolve_shop(args.shop, args.shop_id)

    if args.dups:
        check_dups(shop_id)
        return 0

    if args.user:
        check_orders(args.user)
        asyncio.run(check_redis(args.user))
    else:
        print("\n(skip CHECK 1/3 — pass --user <telegram id> to inspect orders + Redis)")

    check_dups(shop_id)

    if args.probe:
        asyncio.run(check_probe(shop_id, args.last_interest))
    else:
        print("\n(skip CHECK 4 — pass --probe to reproduce the search miss via the LLM)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
