"""市场雷达只读 HTTP 契约测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.web_api import seed_market_radar_data, web_settings
from trading_assistant.market_radar.metrics import BreadthMetric
from trading_assistant.web_api.app import create_app


def test_price_radar_endpoints_expose_only_complete_snapshot(tmp_path: Path) -> None:
    seed_market_radar_data(tmp_path)
    client = TestClient(create_app(web_settings(tmp_path, account="")))

    summary = client.get("/api/market-radar/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["source_state"] == "available"
    assert payload["as_of_date"] == "2026-09-02"
    assert payload["coverage"] == {"eligible": 25, "observed": 25, "ratio": 1.0}
    assert payload["market"]["spy_return_20"]["value"] == 0.04
    assert [item["state"] for item in payload["modules"]] == [
        "complete",
        "complete",
        "complete",
        "unavailable",
        "unavailable",
        "unavailable",
    ]

    breadth = client.get("/api/market-radar/breadth")
    assert breadth.status_code == 200
    breadth_payload = breadth.json()
    assert breadth_payload["source_state"] == "available"
    assert breadth_payload["validity"] == "complete"
    assert breadth_payload["membership_source"] == "state_street_spy_holdings"
    assert breadth_payload["freshness"] == {
        "membership_age_days": 1,
        "stale_after_days": 7,
    }
    assert breadth_payload["b50"] == {
        "value": 0.5,
        "validity": "complete",
        "coverage": {"eligible": 20, "observed": 20, "ratio": 1.0},
        "history_required": 50,
    }
    assert breadth_payload["b200"]["history_required"] == 200

    sectors = client.get("/api/market-radar/sectors")
    assert sectors.status_code == 200
    assert sectors.json()["items"][0]["instrument_id"] == "XLK.US"
    sector = client.get("/api/market-radar/sectors/information_technology")
    assert sector.status_code == 200
    assert sector.json()["relative_strength_60"]["value"] == 0.08

    stocks = client.get(
        "/api/market-radar/stocks",
        params={
            "sector": "information_technology",
            "query": "aapl",
            "sort": "momentum",
            "direction": "desc",
            "offset": 0,
            "limit": 1,
        },
    )
    assert stocks.status_code == 200
    assert stocks.json()["items"][0]["instrument_id"] == "AAPL.US"
    stock = client.get("/api/market-radar/stocks/AAPL.US")
    assert stock.status_code == 200
    assert stock.json()["max_drawdown_126"]["value"] == -0.14


def test_missing_database_and_invalid_entities_have_explicit_boundaries(tmp_path: Path) -> None:
    client = TestClient(create_app(web_settings(tmp_path, account="")))

    summary = client.get("/api/market-radar/summary")
    assert summary.status_code == 200
    assert summary.json()["source_state"] == "missing"
    assert summary.json()["market"] is None
    breadth = client.get("/api/market-radar/breadth")
    assert breadth.status_code == 200
    assert breadth.json()["source_state"] == "missing"
    assert breadth.json()["validity"] == "unavailable"
    assert breadth.json()["b50"] is None
    assert client.get("/api/market-radar/sectors/unknown").status_code == 404
    assert client.get("/api/market-radar/stocks/AAPL.US").status_code == 404

    invalid = client.get("/api/market-radar/stocks?sort=not-a-field")
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")


def test_corrupt_market_database_returns_redacted_problem(tmp_path: Path) -> None:
    database = tmp_path / "market-radar.db"
    database.write_text("/private/token/should-not-leak", encoding="utf-8")
    client = TestClient(
        create_app(web_settings(tmp_path, account="")), raise_server_exceptions=False
    )

    response = client.get("/api/market-radar/summary")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "source_unavailable"
    assert "token" not in response.text


@pytest.mark.parametrize(
    ("metric", "membership_age_days", "expected"),
    [
        (BreadthMetric(0.3, "partial", 20, 18, 0.9, 50), 1, "partial"),
        (
            BreadthMetric(None, "insufficient_coverage", 20, 17, 0.85, 50),
            1,
            "insufficient_coverage",
        ),
        (BreadthMetric(0.5, "complete", 20, 20, 1, 50), 8, "stale"),
    ],
)
def test_breadth_endpoint_preserves_validity_states(
    tmp_path: Path,
    metric: BreadthMetric,
    membership_age_days: int,
    expected: str,
) -> None:
    seed_market_radar_data(
        tmp_path,
        breadth_metric=metric,
        membership_age_days=membership_age_days,
    )
    response = TestClient(create_app(web_settings(tmp_path, account=""))).get(
        "/api/market-radar/breadth"
    )

    assert response.status_code == 200
    assert response.json()["validity"] == expected
    assert response.json()["b50"]["validity"] == metric.validity
    assert response.json()["b50"]["value"] == metric.value


def test_corrupt_breadth_isolated_from_price_summary(tmp_path: Path) -> None:
    seed_market_radar_data(tmp_path)
    with sqlite3.connect(tmp_path / "market-radar.db") as connection:
        connection.execute(
            "UPDATE current_breadth_snapshots SET payload_json = ?",
            ('{"unexpected": true}',),
        )
    client = TestClient(
        create_app(web_settings(tmp_path, account="")), raise_server_exceptions=False
    )

    breadth = client.get("/api/market-radar/breadth")
    assert breadth.status_code == 503
    assert breadth.headers["content-type"].startswith("application/problem+json")
    summary = client.get("/api/market-radar/summary")
    assert summary.status_code == 200
    assert summary.json()["market"]["spy_return_20"]["value"] == 0.04
    breadth_module = next(
        item for item in summary.json()["modules"] if item["module_id"] == "market_breadth"
    )
    assert breadth_module["state"] == "unavailable"
