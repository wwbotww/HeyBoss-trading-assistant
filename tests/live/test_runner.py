"""TradingNode paper-only 离线装配测试。"""

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from nautilus_trader.model.identifiers import AccountId

from trading_assistant.live import runner


def test_builds_paper_trading_node_from_native_components() -> None:
    project_root = Path(__file__).resolve().parents[2]
    config = runner.build_trading_node_config(
        project_root=project_root,
        environ={
            "TRADING_MODE": "paper",
            "TWS_ACCOUNT": "DU123",
            "CATALOG_PATH": str(project_root / "catalog"),
        },
    )
    assert tuple(config.exec_clients) == ("IB",)
    assert len(config.actors) == 2
    assert config.actors[0].config["bootstrap_from_catalog"] is True
    assert config.actors[0].config["stream_bars"] is False
    assert config.actors[1].config["account_id"] == "IB-DU123"
    assert config.actors[1].config["snapshot_interval_seconds"] == 30
    assert config.strategies[0].config["account_id"] == "IB-DU123"
    assert config.exec_clients["IB"].routing.default is True
    assert str(AccountId(config.strategies[0].config["account_id"])) == "IB-DU123"


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"TRADING_MODE": "live", "TWS_ACCOUNT": "U123"}, "paper"),
        ({"TRADING_MODE": "paper"}, "TWS_ACCOUNT"),
    ],
)
def test_rejects_non_paper_or_missing_account(environ: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        runner.build_trading_node_config(project_root=Path.cwd(), environ=environ)


def test_sync_live_catalog_runs_m1_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> None:
        assert command[-1].endswith("scripts/fetch_data.py")
        calls.append(kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner.sync_live_catalog(
        project_root=Path("/project"),
        environ={"CATALOG_PATH": "catalog"},
    )
    assert calls == [
        {
            "cwd": Path("/project"),
            "env": {"CATALOG_PATH": "catalog"},
            "check": True,
        }
    ]


def test_sync_live_catalog_fails_closed_on_child_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_: object, **__: object) -> None:
        raise subprocess.CalledProcessError(1, ["python", "fetch_data.py"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Catalog sync process"):
        runner.sync_live_catalog(project_root=Path.cwd(), environ={})


def test_run_live_builds_runs_and_disposes_node(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_sync(**_: object) -> None:
        calls.append("sync")

    class FakeNode:
        def __init__(self, config: object) -> None:
            assert config == "config"

        def add_exec_client_factory(self, name: str, factory: object) -> None:
            assert name == "IB"
            calls.append("factory")

        def build(self) -> None:
            calls.append("build")

        def run(self, *, raise_exception: bool = False) -> None:
            assert raise_exception is True
            calls.append("run")

        def dispose(self) -> None:
            calls.append("dispose")

    monkeypatch.setattr(runner, "sync_live_catalog", fake_sync)
    monkeypatch.setattr(runner, "build_trading_node_config", lambda **_: "config")
    monkeypatch.setattr(runner, "TradingNode", cast(Any, FakeNode))
    runner.run_live(
        project_root=Path.cwd(),
        environ={"TRADING_MODE": "paper", "TWS_ACCOUNT": "DU123"},
    )
    assert calls == ["sync", "factory", "build", "run", "dispose"]


def test_run_live_does_not_create_node_when_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_sync(**_: object) -> None:
        raise RuntimeError("sync failed")

    monkeypatch.setattr(runner, "sync_live_catalog", fake_sync)
    with pytest.raises(RuntimeError, match="sync failed"):
        runner.run_live(
            project_root=Path.cwd(),
            environ={"TRADING_MODE": "paper", "TWS_ACCOUNT": "DU123"},
        )


def test_run_live_rejects_live_before_sync() -> None:
    with pytest.raises(ValueError, match="paper"):
        runner.run_live(
            project_root=Path.cwd(),
            environ={"TRADING_MODE": "live", "TWS_ACCOUNT": "U123"},
        )
