import os
import uuid
import logging
import pytest
from dotenv import load_dotenv

from config import Config
from kalshi.client import KalshiAPIClient


def _require_demo_env() -> tuple[Config, str]:
    # Load credentials from .env and force demo mode for this test
    load_dotenv()
    os.environ["KALSHI_DEMO_MODE"] = "True"
    cfg = Config()
    demo_ticker = os.getenv("DEMO_TICKER", "")
    missing = []
    # Config already consumed the env; no need to list KALSHI_DEMO_MODE as missing here
    if not getattr(cfg, "KALSHI_API_KEY_ID", None):
        missing.append("KALSHI_API_KEY_ID")
    if not getattr(cfg, "KALSHI_PRIVATE_KEY_PATH", None):
        missing.append("KALSHI_PRIVATE_KEY_PATH")
    if not demo_ticker:
        missing.append("DEMO_TICKER")
    if missing:
        pytest.skip(f"Demo live test skipped; missing: {', '.join(missing)}")
    return cfg, demo_ticker


@pytest.mark.integration
def test_live_mode_places_and_cancels_in_demo_api(caplog: pytest.LogCaptureFixture):
    demo_env, demo_ticker = _require_demo_env()

    api = KalshiAPIClient(demo_env)

    # Show strategy network logs during the test run
    caplog.set_level(logging.INFO, logger="market_making_bot.strategy")

    # 1) Create order group
    og_resp = api.http_client.make_authenticated_request(
        "POST", "/portfolio/order_groups/create", json_data={"contracts_limit": 5}
    )
    print(f"CREATE_GROUP status={og_resp.status_code} body={og_resp.text}")
    assert 200 <= og_resp.status_code < 300, f"group status={og_resp.status_code} body={og_resp.text}"
    group_id = og_resp.json().get("order_group_id")
    assert group_id, "missing order_group_id"

    # 2) Place a tiny post-only order in the demo market with unique client order id
    client_order_id = f"touch-live-{uuid.uuid4().hex[:16]}"
    order_payload = {
        "action": "buy",
        "side": "yes",
        "ticker": demo_ticker,
        "yes_price": 5,
        "count": 1,
        "post_only": True,
        "client_order_id": client_order_id,
        "order_group_id": group_id,
    }
    o_resp = api.http_client.make_authenticated_request("POST", "/portfolio/orders", json_data=order_payload)
    print(f"CREATE_ORDER status={o_resp.status_code} body={o_resp.text}")
    assert 200 <= o_resp.status_code < 300, f"order status={o_resp.status_code} body={o_resp.text}"
    order_id = (o_resp.json().get("order") or {}).get("order_id")
    assert order_id, f"missing order_id body={o_resp.text}"

    # 3) Cancel the order by order_id (preferred)
    del_resp = api.http_client.make_authenticated_request("DELETE", f"/portfolio/orders/{order_id}", json_data={})
    print(f"CANCEL_ORDER status={del_resp.status_code} body={del_resp.text}")
    assert del_resp.status_code in (200, 204), f"cancel status={del_resp.status_code} body={del_resp.text}"

    # Also print captured strategy logs (LIVE POST lines)
    if caplog.text:
        print("\n--- strategy network logs ---\n" + caplog.text)

    # Note: optional follow-up could poll GET /portfolio/order to assert canceled status if needed

