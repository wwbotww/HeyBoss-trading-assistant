"""账户 API 测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.web_api import seed_web_data, web_settings
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
