"""策略、因子、回测和数据 API 测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.web_api import seed_web_data, web_settings
from trading_assistant.web_api.app import create_app


def test_research_api_uses_whitelisted_sources_and_fixed_report_paths(tmp_path: Path) -> None:
    seed_web_data(tmp_path)
    client = TestClient(create_app(web_settings(tmp_path)))

    assert client.get("/api/strategy/active").json()["name"] == "patchtst_e3"
    factor = client.get("/api/factors/latest").json()
    assert factor["source_state"] == "available"
    assert len(factor["scores"]) == 3
    catalog = client.get("/api/data/catalog").json()
    assert catalog["provider"] == "eodhd"
    assert any(item["state"] == "available" for item in catalog["coverage"])
    quality = client.get("/api/data/quality/latest").json()
    assert quality["source_state"] == "available"

    backtests = client.get("/api/backtests").json()
    assert backtests["items"][0]["run_id"] == "web-run"
    detail = client.get("/api/backtests/web-run").json()
    assert set(detail["available_tables"]) == {
        "equity",
        "orders",
        "fills",
        "positions",
        "account",
    }
    for table in detail["available_tables"]:
        response = client.get(f"/api/backtests/web-run/{table}?limit=1")
        assert response.status_code == 200
        assert response.json()["rows"][0]["id"] == 1

    traversal = client.get("/api/backtests/%2E%2E%2Fsecret")
    assert traversal.status_code in {404, 422}
    assert str(tmp_path) not in traversal.text
