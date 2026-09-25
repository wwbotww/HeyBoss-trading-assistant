"""账户 API 测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from tests.web_api import seed_web_data, web_settings
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.web_api.app import create_app


def test_portfolio_api_is_read_only_masked_and_paginated(tmp_path: Path) -> None:
    seed_web_data(tmp_path)
    client = TestClient(create_app(web_settings(tmp_path)))

    response = client.get("/api/portfolio")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_state"] == "available"
    assert payload["account_id"] == "IB-••••U123"
    assert "DU123" not in response.text
    assert payload["positions"][0]["canonical_id"] == "AAPL.US"
    assert payload["positions"][0]["reference_price"] == 210
    history = client.get("/api/portfolio/history?offset=0&limit=1")
    assert history.status_code == 200
    assert history.json()["items"][0]["account_id"] == "IB-••••U123"
    assert str(tmp_path) not in response.text


def test_portfolio_api_reports_missing_and_corrupt_sources_safely(tmp_path: Path) -> None:
    missing = TestClient(create_app(web_settings(tmp_path)))
    assert missing.get("/api/portfolio").json()["source_state"] == "missing"

    corrupt = tmp_path / "live.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    response = TestClient(
        create_app(web_settings(tmp_path)),
        raise_server_exceptions=False,
    ).get("/api/portfolio")
    assert response.status_code == 503
    assert response.json()["code"] == "source_unavailable"
    assert str(tmp_path) not in response.text
    assert "sqlite" not in response.text.lower()


def test_empty_positions_retain_readiness_and_recovery_fields_in_both_endpoints(
    tmp_path: Path,
) -> None:
    seed_web_data(tmp_path)
    now = datetime.now(UTC)
    writer = TradingRepository(f"sqlite:///{tmp_path / 'live.db'}")
    client = TestClient(create_app(web_settings(tmp_path)))
    try:
        for index, reconciled in enumerate((False, True)):
            observed = now - timedelta(seconds=2 - index)
            writer.record_portfolio_snapshot(
                timestamp_ns=int(observed.timestamp() * 1e9),
                account_id="IB-DU123",
                currency="USD",
                net_liquidation=10000,
                available_funds=10000,
                total_cash_value=10000,
                positions=(),
                account_updated_at_utc=observed,
                broker_connected=True,
                reconciliation_complete=reconciled,
                broker_stale_after_seconds=300,
            )
            portfolio = client.get("/api/portfolio").json()
            overview = client.get("/api/overview").json()["portfolio"]
            for snapshot in (portfolio, overview):
                assert snapshot["source_state"] == "available"
                assert snapshot["positions"] == []
                assert snapshot["reconciliation_complete"] is reconciled
                assert snapshot["is_stale"] is not reconciled
                assert snapshot["account_updated_at_utc"] is not None
                assert bool(snapshot["not_ready_reason"]) is not reconciled
    finally:
        writer.close()
