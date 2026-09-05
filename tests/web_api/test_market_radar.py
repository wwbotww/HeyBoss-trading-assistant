"""市场雷达只读 HTTP 契约测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.web_api import seed_fundamental_data, seed_market_radar_data, seed_web_data, web_settings
from trading_assistant.market_radar.metrics import BreadthMetric
from trading_assistant.web_api.app import create_app


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_market_database_failure_does_not_break_other_read_only_pages(
    tmp_path: Path, state: str
) -> None:
    """市场库缺失或损坏不影响交易/研究查询, 也不会通过查询创建或修复文件。"""
    workflow = seed_web_data(tmp_path)
    market = tmp_path / "market-radar.db"
    if state == "corrupt":
        market.write_bytes(b"invalid market database")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with TestClient(create_app(web_settings(tmp_path))) as client:
        radar = client.get("/api/market-radar/summary")
        assert radar.status_code == (200 if state == "missing" else 503)
        if state == "missing":
            assert radar.json()["source_state"] == "missing"
        for path in (
            "/api/health",
            "/api/overview",
            "/api/portfolio",
            "/api/strategy/active",
            "/api/factors/latest",
            "/api/workflows",
            f"/api/workflows/{workflow}",
            "/api/orders",
            "/api/orders/O-WEB-1",
            "/api/fills",
            "/api/backtests/web-run",
            "/api/data/catalog",
            "/api/system/status",
        ):
            assert client.get(path).status_code == 200, path
        assert client.get("/api/portfolio").json()["net_liquidation"] == 10_000
        assert client.get("/api/orders").json()["items"][0]["client_order_id"] == "O-WEB-1"
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_events_get_preserves_fields_scope_and_read_only_contract(tmp_path: Path) -> None:
    seed_market_radar_data(tmp_path)
    database = tmp_path / "market-radar.db"
    before = database.read_bytes()
    with TestClient(create_app(web_settings(tmp_path, account=""))) as client:
        response = client.get("/api/market-radar/events")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["days"]) == 14
        assert payload["window_start"] == payload["days"][0]["day"]
        assert payload["window_end"] == payload["days"][-1]["day"]
        for key in ("economic_source", "earnings_source"):
            assert payload[key]["source_state"] == "available"
            assert payload[key]["window_event_count"] == 1
            assert payload[key]["coverage"]["covered_days"] == 14
            assert payload[key]["freshness"]["stale_after_seconds"] == 86400
        assert payload["earnings_source"]["watchlist_count"] == 2
        economic = payload["days"][0]["economic_events"][0]
        earnings = payload["days"][0]["earnings_events"][0]
        assert economic["actual"] == 0
        assert economic["estimate"] is economic["source_time"] is None
        assert "event_time_utc" not in economic
        assert "importance" not in economic
        assert earnings["session"] == "unknown"
        assert earnings["currency"] is None
        assert earnings["actual_eps"] == 0
        assert earnings["estimated_eps"] == -0.1
        assert "available_at_utc" not in earnings
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, "/api/market-radar/events").status_code == 405
        route = client.get("/openapi.json").json()["paths"]["/api/market-radar/events"]["get"]
        assert route["operationId"] == "getMarketRadarEvents"
        assert not route.get("parameters")
    assert database.read_bytes() == before


@pytest.mark.parametrize("state", ["missing", "empty", "old_schema"])
def test_event_missing_sources_do_not_create_database_or_tables(tmp_path: Path, state: str) -> None:
    database = tmp_path / "market-radar.db"
    if state != "missing":
        with sqlite3.connect(database) as connection:
            if state == "old_schema":
                connection.execute("CREATE TABLE legacy (sample TEXT)")
    before = database.read_bytes() if database.exists() else None
    with TestClient(create_app(web_settings(tmp_path, account=""))) as client:
        response = client.get("/api/market-radar/events")
        assert response.status_code == 200
        payload = response.json()
        for key in ("economic_source", "earnings_source"):
            assert payload[key]["source_state"] == ("missing" if state == "missing" else "empty")
            assert payload[key]["coverage"] is payload[key]["window_event_count"] is None
        assert len(payload["days"]) == 14
        assert all(
            not day["economic_events"] and not day["earnings_events"] for day in payload["days"]
        )
    assert (database.read_bytes() if database.exists() else None) == before


@pytest.mark.parametrize("case", ["economic", "earnings", "both", "unreadable", "unrelated"])
def test_event_http_errors_are_source_isolated_and_sanitized(tmp_path: Path, case: str) -> None:
    seed_market_radar_data(tmp_path)
    database = tmp_path / "market-radar.db"
    with sqlite3.connect(database) as connection:
        if case in {"economic", "both"}:
            connection.execute(
                "UPDATE economic_event_snapshots SET payload_json = ?", ('{"token":"secret"}',)
            )
        if case in {"earnings", "both"}:
            connection.execute("UPDATE earnings_calendar_events SET session = ?", ("private-time",))
        if case == "unrelated":
            connection.execute("DROP TABLE earnings_trend_observations")
            connection.execute("DROP TABLE fundamental_observations")
            connection.execute(
                "UPDATE price_snapshots SET payload_json = ?", ('{"token":"secret"}',)
            )
    if case == "unreadable":
        database.write_bytes(b"not a database: secret")
    before = database.read_bytes()
    with TestClient(create_app(web_settings(tmp_path, account=""))) as client:
        response = client.get("/api/market-radar/events")
        assert "secret" not in response.text
        assert "private-time" not in response.text
        assert str(database) not in response.text
        if case in {"both", "unreadable"}:
            assert response.status_code == 503
            assert response.headers["content-type"].startswith("application/problem+json")
        else:
            assert response.status_code == 200
            payload = response.json()
            assert payload["economic_source"]["source_state"] == (
                "invalid" if case == "economic" else "available"
            )
            assert payload["earnings_source"]["source_state"] == (
                "invalid" if case == "earnings" else "available"
            )
    assert database.read_bytes() == before


def test_fundamentals_get_returns_published_batch_without_price_intersection(
    tmp_path: Path,
) -> None:
    snapshot = seed_fundamental_data(tmp_path)
    database = tmp_path / "market-radar.db"
    before = database.read_bytes()
    client = TestClient(create_app(web_settings(tmp_path, account="")))
    response = client.get("/api/market-radar/fundamentals")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_state"] == "available"
    assert payload["validity"] == snapshot.validity
    assert payload["as_of_date"] == "2026-09-05"
    assert payload["source"] == "eodhd_fundamentals"
    assert [item["instrument_id"] for item in payload["items"]] == [
        "AAPL.US",
        "JPM.US",
        "REIT.US",
        "UNKNOWN.US",
    ]
    assert [item["metrics"] for item in payload["items"]] == [
        item["metrics"] for item in snapshot.to_payload()["items"]
    ]
    assert client.get("/api/market-radar/stocks/JPM.US").status_code == 404
    assert len(client.get("/api/market-radar/summary").json()["modules"]) == 6
    assert client.post("/api/market-radar/fundamentals").status_code == 405
    route = client.get("/openapi.json").json()["paths"]["/api/market-radar/fundamentals"]["get"]
    assert route["operationId"] == "getMarketRadarFundamentals"
    assert not route.get("parameters")
    assert database.read_bytes() == before


@pytest.mark.parametrize("state", ["missing", "empty", "old_schema"])
def test_fundamentals_missing_or_old_database_never_creates_tables(
    tmp_path: Path, state: str
) -> None:
    database = tmp_path / "market-radar.db"
    if state == "old_schema":
        seed_market_radar_data(tmp_path)
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE fundamental_snapshots")
            connection.execute("DROP TABLE fundamental_observations")
    elif state == "empty":
        with sqlite3.connect(database):
            pass
    before = database.read_bytes() if database.exists() else None
    client = TestClient(create_app(web_settings(tmp_path, account="")))
    response = client.get("/api/market-radar/fundamentals")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_state"] == ("missing" if state == "missing" else "empty")
    assert payload["validity"] == "unavailable"
    assert payload["freshness"] is None
    assert payload["as_of_date"] is None
    assert payload["calculated_at_utc"] is None
    assert payload["source"] is None
    assert payload["items"] == []
    assert (database.read_bytes() if database.exists() else None) == before


def test_fundamentals_query_never_reads_inputs_or_recomputes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_fundamental_data(tmp_path)
    with sqlite3.connect(tmp_path / "market-radar.db") as connection:
        connection.execute("DROP TABLE fundamental_observations")

    def forbidden(**kwargs: object) -> None:
        raise AssertionError("query must not recalculate fundamentals")

    monkeypatch.setattr(
        "trading_assistant.market_radar.storage.calculate_fundamental_snapshot", forbidden
    )
    client = TestClient(create_app(web_settings(tmp_path, account="")))
    assert client.get("/api/market-radar/fundamentals").status_code == 200


@pytest.mark.parametrize(
    "case",
    [
        "payload",
        "metadata",
        "future_source",
        "future_period",
        "schema",
        "invalid_json",
        "invalid_time",
    ],
)
def test_corrupt_fundamentals_fail_closed_without_breaking_other_modules(
    tmp_path: Path, case: str
) -> None:
    seed_market_radar_data(tmp_path)
    seed_fundamental_data(tmp_path)
    with sqlite3.connect(tmp_path / "market-radar.db") as connection:
        if case == "schema":
            connection.execute("DROP TABLE fundamental_snapshots")
            connection.execute("CREATE TABLE fundamental_snapshots (wrong_column TEXT)")
        elif case == "metadata":
            connection.execute("UPDATE fundamental_snapshots SET as_of_date = '2000-01-01'")
        elif case == "invalid_json":
            connection.execute("UPDATE fundamental_snapshots SET payload_json = '{broken'")
        elif case == "invalid_time":
            connection.execute("UPDATE fundamental_snapshots SET calculated_at_utc = 'not-a-time'")
        else:
            payload = json.loads(
                connection.execute("SELECT payload_json FROM fundamental_snapshots").fetchone()[0]
            )
            if case == "payload":
                payload = {"secret": "/private/api-token-must-not-leak"}
            elif case == "future_source":
                payload["items"][0]["source_updated_date"] = "2099-01-01"
            else:
                payload["items"][0]["metrics"][0]["period_end"] = "2099-01-01"
            connection.execute(
                "UPDATE fundamental_snapshots SET payload_json = ?", (json.dumps(payload),)
            )
    client = TestClient(create_app(web_settings(tmp_path, account="")))
    response = client.get("/api/market-radar/fundamentals")
    assert response.status_code == 503
    assert response.json()["code"] == "source_unavailable"
    assert "/private" not in response.text
    assert "wrong_column" not in response.text
    summary = client.get("/api/market-radar/summary")
    assert summary.status_code == 200
    assert len(summary.json()["modules"]) == 6
    for path in ("stocks/AAPL.US", "breadth", "earnings", "macro"):
        assert client.get(f"/api/market-radar/{path}").status_code == 200


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
        "complete",
        "complete",
        "complete",
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

    macro = client.get("/api/market-radar/macro")
    assert macro.status_code == 200
    macro_payload = macro.json()
    assert macro_payload["source_state"] == "available"
    assert macro_payload["validity"] == "complete"
    assert macro_payload["current"]["regime"] == "easing_risk_on"
    assert macro_payload["current"]["regime_label"] == "宽松型 Risk-on"
    assert macro_payload["real_rate"]["series_id"] == "DFII10"
    assert macro_payload["risk_appetite"]["score"] == 0.6

    earnings = client.get("/api/market-radar/earnings")
    assert earnings.status_code == 200
    earnings_payload = earnings.json()
    assert earnings_payload["source_state"] == "available"
    assert earnings_payload["validity"] == "complete"
    assert earnings_payload["source"] == "eodhd_calendar"
    assert earnings_payload["freshness"] == {
        "snapshot_age_days": 0,
        "stale_after_days": 3,
    }
    assert earnings_payload["membership"]["classification_validity"] == "complete"
    assert earnings_payload["market"]["eligible"] == 2
    assert earnings_payload["market"]["observed"] == 2
    assert earnings_payload["market"]["breadth"] == 0
    assert earnings_payload["sectors"][0]["sector_id"] == "information_technology"

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
    macro = client.get("/api/market-radar/macro")
    assert macro.status_code == 200
    assert macro.json()["source_state"] == "missing"
    assert macro.json()["current"] is None
    earnings = client.get("/api/market-radar/earnings")
    assert earnings.status_code == 200
    assert earnings.json()["source_state"] == "missing"
    assert earnings.json()["market"] is None
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


def test_corrupt_macro_isolated_from_price_and_breadth(tmp_path: Path) -> None:
    seed_market_radar_data(tmp_path)
    with sqlite3.connect(tmp_path / "market-radar.db") as connection:
        connection.execute(
            "UPDATE macro_regime_snapshots SET payload_json = ?",
            ('{"unexpected": true}',),
        )
    client = TestClient(
        create_app(web_settings(tmp_path, account="")), raise_server_exceptions=False
    )

    macro = client.get("/api/market-radar/macro")
    assert macro.status_code == 503
    assert "token" not in macro.text
    summary = client.get("/api/market-radar/summary")
    assert summary.status_code == 200
    assert summary.json()["market"]["spy_return_20"]["value"] == 0.04
    modules = {item["module_id"]: item for item in summary.json()["modules"]}
    assert modules["market_breadth"]["state"] == "complete"
    assert modules["real_rates"]["state"] == "unavailable"


def test_corrupt_earnings_isolated_from_other_market_modules(tmp_path: Path) -> None:
    seed_market_radar_data(tmp_path)
    with sqlite3.connect(tmp_path / "market-radar.db") as connection:
        connection.execute(
            "UPDATE earnings_revision_snapshots SET payload_json = ?",
            ('{"unexpected": true}',),
        )
    client = TestClient(
        create_app(web_settings(tmp_path, account="")), raise_server_exceptions=False
    )

    earnings = client.get("/api/market-radar/earnings")
    assert earnings.status_code == 503
    assert earnings.headers["content-type"].startswith("application/problem+json")
    assert "token" not in earnings.text
    summary = client.get("/api/market-radar/summary")
    assert summary.status_code == 200
    assert summary.json()["market"]["spy_return_20"]["value"] == 0.04
    modules = {item["module_id"]: item for item in summary.json()["modules"]}
    assert modules["market_breadth"]["state"] == "complete"
    assert modules["real_rates"]["state"] == "complete"
    assert modules["earnings_revisions"]["state"] == "unavailable"
