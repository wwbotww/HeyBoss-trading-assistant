"""每日命令只读健康检查和错误脱敏。"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from trading_assistant.market_radar.storage import DailySyncWorker, MarketRadarRepository

_SPEC = spec_from_file_location(
    "sync_market_daily", Path(__file__).resolve().parents[2] / "scripts/sync_market_daily.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
cli = module_from_spec(_SPEC)
_SPEC.loader.exec_module(cli)


@pytest.mark.parametrize(
    "state", ["missing", "empty", "healthy", "stale", "stopped", "future", "corrupt"]
)
def test_healthcheck_is_read_only_without_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setenv("MARKET_RADAR_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("MARKET_RADAR_CATALOG_PATH", str(tmp_path / "catalog"))
    monkeypatch.setattr(sys, "argv", ["sync_market_daily.py", "--healthcheck"])
    if state != "missing":
        repository = MarketRadarRepository(f"sqlite:///{database}")
        repository.create_schema()
        if state != "empty":
            now = datetime.now(UTC)
            worker = DailySyncWorker(
                now, now, None, None, now + timedelta(days=1), time(6), 60, 3600, 4
            )
            if state == "stale":
                worker = replace(worker, heartbeat_at_utc=now - timedelta(minutes=6))
            if state == "future":
                worker = replace(worker, heartbeat_at_utc=now + timedelta(minutes=1))
            if state == "stopped":
                worker = replace(worker, stopped_at_utc=now)
            repository.update_daily_worker(worker)
        repository.close()
        if state == "corrupt":
            database.write_bytes(b"not sqlite")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert cli.main() == (0 if state == "healthy" else 1)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_once_without_credentials_leaves_actionable_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HEYBOSS_RUNTIME_ROOT", str(tmp_path))
    for key in (
        "MARKET_RADAR_DATABASE_URL",
        "MARKET_RADAR_CATALOG_PATH",
        "EODHD_API_TOKEN",
        "FRED_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "argv", ["sync_market_daily.py", "--once"])
    assert cli.main() == 1
    output = capsys.readouterr().out
    assert "job=economic_events state=failed" in output
    assert "job=breadth state=waiting_dependency" in output
    repository = MarketRadarRepository(
        f"sqlite:///{tmp_path}/market-radar/data/market-radar.db", read_only=True
    )
    assert len(repository.daily_attempts()) == 5
    assert all(item.next_retry_at_utc is None for item in repository.daily_attempts())
    repository.close()
