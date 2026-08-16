"""操作总览与系统状态 API 测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.web_api import seed_web_data, web_settings
from trading_assistant.web_api.app import create_app


def test_overview_and_system_status_do_not_infer_runtime_connectivity(tmp_path: Path) -> None:
    seed_web_data(tmp_path)
    client = TestClient(create_app(web_settings(tmp_path)))

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["portfolio"]["net_liquidation"] == 10_000
    assert payload["active_strategy"]["name"] == "patchtst_e3"
    assert payload["latest_factor"]["source_state"] == "available"
    assert payload["workflow_status_counts"] == {"NEW": 1}
    assert payload["latest_fill_at_utc"] is not None

    status = client.get("/api/system/status").json()
    by_name = {item["name"]: item for item in status["sources"]}
    assert by_name["web_api"]["state"] == "available"
    assert by_name["live_database"]["state"] == "available"
    assert by_name["trading_node"]["state"] == "unobserved"
    assert by_name["ibkr"]["state"] == "unobserved"
    assert "DU123" not in client.get("/api/system/status").text
