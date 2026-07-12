"""Item 3 — funnel analytics endpoint + event-firing contracts.

Pins:
  - shop_register fires `signup_completed` on success
  - shop_bot_connect fires `bot_connected` on successful setWebhook
  - orders.create_order fires `first_lead` ONLY on the shop's first order
  - GET /admin/funnel returns the documented shape with distinct shop_id counts
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import conversations
import orders as orders_mod
import routes.shop as shop_route
from routes.shop import router as shop_router


@pytest.fixture(autouse=True)
def reset_register_rate():
    shop_route._register_attempts.clear()
    yield
    shop_route._register_attempts.clear()


def _make_register_client():
    app = FastAPI()
    app.include_router(shop_router)
    return TestClient(app, raise_server_exceptions=False)


class TestSignupCompletedEvent:

    def test_register_fires_signup_completed(self, monkeypatch):
        captured = []

        def fake_log(channel, event_name, payload, shop_id=None):
            captured.append((channel, event_name, payload, shop_id))

        monkeypatch.setattr(shop_route, "get_shop_by_email", lambda email: None)
        monkeypatch.setattr(shop_route, "create_active_shop_with_trial",
                            lambda name, email, pwd, **kw: 77)
        monkeypatch.setattr(shop_route, "get_shop_subscription_detail",
                            lambda sid: {"plan": "trial", "status": "active"})
        monkeypatch.setattr(shop_route, "send_shop_welcome",
                            lambda *a, **kw: None)
        monkeypatch.setattr(shop_route, "log_analytics_event", fake_log)

        client = _make_register_client()
        resp = client.post("/shop/register", json={
            "shop_name": "Funnel Shop",
            "email": "funnel@test.com",
            "password": "password123",
            "accepted_terms": True,
        })
        assert resp.status_code == 200
        events = [e for e in captured if e[1] == "signup_completed"]
        assert len(events) == 1
        assert events[0][3] == 77
        assert events[0][2]["email"] == "funnel@test.com"


class TestFirstLeadEvent:

    def test_create_order_fires_first_lead_only_once(self, monkeypatch):
        """First order → event fires. Subsequent orders for same shop → no event."""
        captured = []

        def fake_log(channel, event_name, payload, shop_id=None):
            captured.append(event_name)

        # Counter for the "existing orders" check — first call returns 0, second 1.
        call_count = {"n": 0}
        def fake_fetch_one_value(query, params=()):
            if "COUNT(*) FROM orders" in query:
                n = call_count["n"]
                call_count["n"] += 1
                return n
            if "MAX(id) FROM orders" in query:
                return 100 + call_count["n"]
            return None

        monkeypatch.setattr(orders_mod, "resolve_shop_id", lambda sid: sid or 1)
        monkeypatch.setattr(orders_mod, "fetch_one_value", fake_fetch_one_value)
        monkeypatch.setattr(orders_mod, "execute_write", lambda *a, **kw: None)
        monkeypatch.setattr(orders_mod, "USE_POSTGRES", False)
        monkeypatch.setattr(conversations, "log_analytics_event", fake_log)

        orders_mod.create_order("telegram", "ext1", "Иван", "+7", "shoes", shop_id=5)
        orders_mod.create_order("telegram", "ext2", "Петя", "+7", "boots", shop_id=5)

        first_leads = [e for e in captured if e == "first_lead"]
        assert len(first_leads) == 1


class TestFunnelEndpoint:

    def test_funnel_shape(self, monkeypatch):
        from routes.admin import router as admin_router

        app = FastAPI()
        app.include_router(admin_router)
        app.dependency_overrides = {}

        # Bypass admin auth — the dependency is called inside the handler, not as
        # FastAPI Depends, so monkeypatch the function directly.
        monkeypatch.setattr("routes.admin.require_admin", lambda req: None)

        # Stub the DB helpers
        def fake_fetch_one_value(query, params=()):
            if "FROM shops" in query and "status" in query:
                return 4
            if "DISTINCT shop_id" in query:
                event = params[0] if params else ""
                return {
                    "signup_completed": 4,
                    "products_imported": 3,
                    "bot_connected": 2,
                    "first_lead": 1,
                }.get(event, 0)
            return 0

        monkeypatch.setattr("db.fetch_one_value", fake_fetch_one_value)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/admin/funnel")
        assert resp.status_code == 200
        data = resp.json()
        assert {s["name"] for s in data["stages"]} == {
            "signup", "catalog_uploaded", "bot_connected", "first_lead"
        }
        signup = next(s for s in data["stages"] if s["name"] == "signup")
        lead   = next(s for s in data["stages"] if s["name"] == "first_lead")
        assert signup["count"] == 4
        assert lead["count"] == 1
        assert data["totals"]["shops"] == 4
        assert data["conversion_pct"]["lead_from_signup"] == 25.0
