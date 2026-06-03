"""CLI: create or reset a shop owner account.

Usage:
    python create_shop_owner.py --email owner@example.com --password secret123
    python create_shop_owner.py --email owner@example.com --password secret123 --shop-id 2
    python create_shop_owner.py --list
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from auth import hash_password
from db import db_placeholder, execute_write, fetch_all, fetch_one_value
from schema import ensure_app_tables
from shops import get_default_shop_id


def list_shops():
    rows = fetch_all("SELECT id, name, slug, owner_email, status FROM shops ORDER BY id")
    if not rows:
        print("No shops found.")
        return
    print(f"{'ID':>4}  {'Name':<30}  {'Email':<30}  Status")
    print("─" * 80)
    for r in rows:
        print(f"{r['id']:>4}  {r['name']:<30}  {(r['owner_email'] or '—'):<30}  {r['status']}")


def set_owner(email: str, password: str, shop_id: int | None):
    if shop_id is None:
        shop_id = get_default_shop_id()

    ph = db_placeholder()
    existing = fetch_one_value(f"SELECT id FROM shops WHERE id = {ph}", (shop_id,))
    if not existing:
        print(f"Error: shop id={shop_id} not found.")
        sys.exit(1)

    pwd_hash = hash_password(password)
    execute_write(
        f"UPDATE shops SET owner_email = {ph}, owner_password_hash = {ph} WHERE id = {ph}",
        (email, pwd_hash, shop_id),
    )
    print(f"✓ Owner set for shop id={shop_id}")
    print(f"  Email:    {email}")
    print(f"  Login at: /shop (dashboard)")


def main():
    parser = argparse.ArgumentParser(description="Manage SoleBot shop owner accounts")
    parser.add_argument("--email", help="Owner email")
    parser.add_argument("--password", help="Owner password (min 8 chars)")
    parser.add_argument("--shop-id", type=int, help="Shop ID (default: first shop)")
    parser.add_argument("--list", action="store_true", help="List all shops")
    args = parser.parse_args()

    ensure_app_tables()

    if args.list:
        list_shops()
        return

    if not args.email or not args.password:
        parser.print_help()
        sys.exit(1)

    if len(args.password) < 8:
        print("Error: password must be at least 8 characters.")
        sys.exit(1)

    set_owner(args.email, args.password, args.shop_id)


if __name__ == "__main__":
    main()
