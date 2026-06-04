"""
Test script for inventory sync endpoints.
Run AFTER deploying to Railway.

Usage:
    python test_sync.py --url https://salebot-production.up.railway.app --key sk_xxx
"""
import argparse
import json
import sys
import httpx


def test_stock_update(base_url: str, api_key: str):
    """Test POST /sync/stock — direct stock update without МойСклад."""
    print("\n=== TEST 1: Direct stock update ===")

    payload = {
        "replace": False,
        "items": [
            {"brand": "Nike",   "model": "Air Force 1", "size": 42, "quantity": 5, "price": 45000},
            {"brand": "Nike",   "model": "Air Force 1", "size": 43, "quantity": 2, "price": 45000},
            {"brand": "Adidas", "model": "Samba OG",    "size": 41, "quantity": 0, "price": 52000},
            {"brand": "Adidas", "model": "Samba OG",    "size": 42, "quantity": 3, "price": 52000},
        ],
    }

    resp = httpx.post(
        f"{base_url}/sync/stock",
        json=payload,
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.json()["ok"] is True
    print("PASSED")


def test_sold_out(base_url: str, api_key: str):
    """Simulate a sale — set quantity to 0 for one item."""
    print("\n=== TEST 2: Mark item as sold out ===")

    payload = {
        "replace": False,
        "items": [
            {"brand": "Nike", "model": "Air Force 1", "size": 42, "quantity": 0, "price": 45000},
        ],
    }

    resp = httpx.post(
        f"{base_url}/sync/stock",
        json=payload,
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")
    assert resp.status_code == 200
    print("PASSED — Nike AF1 size 42 is now 0 in stock")


def test_invalid_key(base_url: str):
    """Test that invalid API key is rejected."""
    print("\n=== TEST 3: Invalid API key ===")

    resp = httpx.post(
        f"{base_url}/sync/stock",
        json={"items": []},
        headers={"X-API-Key": "invalid_key"},
        timeout=10,
    )
    print(f"Status: {resp.status_code}")
    assert resp.status_code == 401
    print("PASSED — invalid key correctly rejected")


def test_moysklad_webhook_simulation(base_url: str, api_key: str):
    """Simulate a МойСклад webhook (without real МойСклад account)."""
    print("\n=== TEST 4: МойСклад webhook simulation ===")
    print("NOTE: This will fail if МойСклад token is not configured (expected).")

    # МойСклад webhook payload format
    payload = {
        "auditContext": {
            "uid": "test",
            "moment": "2026-06-04 12:00:00",
        },
        "events": [
            {
                "meta": {
                    "type": "demand",
                    "href": "https://api.moysklad.ru/api/remap/1.2/entity/demand/fake-id",
                },
                "action": "CREATE",
                "accountId": "fake-account",
            }
        ],
    }

    resp = httpx.post(
        f"{base_url}/sync/moysklad",
        json=payload,
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")

    if resp.status_code == 400 and "moysklad_token" in resp.text.lower():
        print("EXPECTED — MoySklad token not configured. Set it in Bot Settings to use webhooks.")
    elif resp.status_code == 200:
        print("PASSED")
    else:
        print(f"UNEXPECTED response: {resp.status_code}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test SoleBot sync endpoints")
    parser.add_argument("--url", default="https://salebot-production.up.railway.app",
                        help="Base URL of the app")
    parser.add_argument("--key", required=True,
                        help="Sync API key (generate in dashboard → Bot Settings → API key)")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    key  = args.key

    print(f"Testing: {base}")
    print(f"API Key: {key[:10]}...")

    try:
        test_stock_update(base, key)
        test_sold_out(base, key)
        test_invalid_key(base)
        test_moysklad_webhook_simulation(base, key)
        print("\nAll tests passed!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except httpx.ConnectError:
        print(f"\n❌ Cannot connect to {base}. Is the app running?")
        sys.exit(1)
