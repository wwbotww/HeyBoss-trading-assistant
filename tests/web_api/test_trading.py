"""交易活动 API 测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.web_api import seed_web_data, web_settings
from trading_assistant.web_api.app import create_app


def test_trading_api_exposes_linked_read_only_audit_views(tmp_path: Path) -> None:
    event_id = seed_web_data(tmp_path)
    client = TestClient(create_app(web_settings(tmp_path)))

    signals = client.get("/api/signals?instrument_id=AAPL.US")
    assert signals.status_code == 200
    assert signals.json()["items"][0]["event_id"] == event_id
    workflows = client.get("/api/workflows?status=NEW")
    assert workflows.json()["items"][0]["target_count"] == 1
    detail = client.get(f"/api/workflows/{event_id}")
    assert {item["kind"] for item in detail.json()["timeline"]} == {
        "signal",
        "decision",
        "order",
        "fill",
    }

    orders = client.get("/api/orders?status=FILLED")
    assert orders.json()["items"][0]["client_order_id"] == "O-WEB-1"
    order = client.get("/api/orders/O-WEB-1")
    assert order.json()["summary"]["fill_count"] == 1
    fills = client.get("/api/fills?instrument_id=AAPL.US")
    assert fills.json()["items"][0]["trade_id"] == "T-WEB-1"
    assert client.get("/api/orders/missing").status_code == 404
    assert client.get("/api/workflows/missing").status_code == 404
