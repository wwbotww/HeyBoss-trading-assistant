"""雷达 CLI 测试的默认环境也必须与正式目录隔离。"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_cli_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEYBOSS_RUNTIME_ROOT", str(tmp_path / "cli-runtime"))
    for key in (
        "MARKET_RADAR_CATALOG_PATH",
        "MARKET_RADAR_DATABASE_URL",
        "MARKET_RADAR_REPORT_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
