"""FastAPI 工厂、OpenAPI 与统一错误边界测试。"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient

from tests.web_api import web_settings
from trading_assistant.web_api.app import create_app
from trading_assistant.web_api.config import WebApiSettings, sqlite_database_path
from trading_assistant.web_api.dependencies import get_services


def test_health_openapi_and_error_contract_are_stable(tmp_path: Path) -> None:
    app = create_app(web_settings(tmp_path, account=""))
    client = TestClient(app)

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    schema = client.get("/openapi.json").json()
    expected_paths = {
        "/api/health",
        "/api/overview",
        "/api/portfolio",
        "/api/portfolio/history",
        "/api/strategy/active",
        "/api/factors/latest",
        "/api/signals",
        "/api/workflows",
        "/api/workflows/{event_id}",
        "/api/orders",
        "/api/orders/{client_order_id}",
        "/api/fills",
        "/api/backtests",
        "/api/backtests/{run_id}",
        "/api/backtests/{run_id}/equity",
        "/api/backtests/{run_id}/orders",
        "/api/backtests/{run_id}/fills",
        "/api/backtests/{run_id}/positions",
        "/api/backtests/{run_id}/account",
        "/api/data/catalog",
        "/api/data/quality/latest",
        "/api/system/status",
        "/api/market-radar/summary",
        "/api/market-radar/breadth",
        "/api/market-radar/sectors",
        "/api/market-radar/sectors/{sector_id}",
        "/api/market-radar/stocks",
        "/api/market-radar/stocks/{instrument_id}",
    }
    assert expected_paths <= set(schema["paths"])
    assert not any(path.startswith("/api/v") for path in schema["paths"])
    for path, methods in schema["paths"].items():
        if path.startswith("/api/market-radar"):
            assert set(methods) == {"get"}
    operation_ids = [
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))

    invalid = client.get("/api/portfolio/history?limit=0")
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert invalid.json() == {
        "code": "invalid_request",
        "title": "Invalid request",
        "detail": "请求参数不符合 API 契约。",
    }
    missing = client.get("/api/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_settings_and_unexpected_errors_do_not_expose_internal_values(tmp_path: Path) -> None:
    assert sqlite_database_path("sqlite:///./data.db", tmp_path) == tmp_path / "data.db"
    assert sqlite_database_path("sqlite:///:memory:", tmp_path) is None
    assert sqlite_database_path("postgresql://localhost/db", tmp_path) is None
    settings = WebApiSettings.from_environment({}, project_root=tmp_path)
    assert settings.market_radar_database_path == tmp_path / "data" / "market-radar.db"
    with pytest.raises(ValueError, match="positive"):
        WebApiSettings.from_environment(
            {"PORTFOLIO_SNAPSHOT_STALE_SECONDS": "0"},
            project_root=tmp_path,
        )

    app = create_app(web_settings(tmp_path, account=""))

    def fail_services() -> NoReturn:
        raise RuntimeError("/private/secret/token-value")

    app.dependency_overrides[get_services] = fail_services
    response = TestClient(app, raise_server_exceptions=False).get("/api/portfolio")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text
    assert "token-value" not in response.text
