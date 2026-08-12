"""TradingNode paper-only 离线装配测试。"""

import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.identifiers import AccountId

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.live import runner

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _patchtst_project(
    tmp_path: Path,
    *,
    source_kind: str | None,
    batch_size: int = 1,
) -> Path:
    """构造只用于 TradingNode 装配的因子策略项目配置。"""
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    strategy_path = tmp_path / "config" / "strategies.yaml"
    values = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
    assert isinstance(values, dict)
    values["active_strategy"] = "patchtst_e3"
    strategy_path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    catalog_path = tmp_path / "catalog"
    if source_kind is not None:
        score = FactorScoreData(
            canonical_id="SPY.US",
            security_id="eodhd:isin:SPY",
            asof_date="2025-01-02",
            score=1.0,
            eligible=True,
            batch_id="delivery:2025-01-02",
            batch_size=batch_size,
            delivery_id="d" * 64,
            model_release_id="r" * 64,
            source_kind=source_kind,
            ts_event=1,
            ts_init=1,
        )
        CatalogRepository(catalog_path).catalog.write_data([CustomData(FACTOR_DATA_TYPE, score)])
    return catalog_path


def test_builds_paper_trading_node_from_native_components() -> None:
    project_root = PROJECT_ROOT
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
    assert config.actors[0].config["strategy_name"] == "dual_momentum"
    assert config.actors[0].config["bootstrap_from_catalog"] is True
    assert config.actors[0].config["stream_bars"] is False
    assert config.actors[1].config["account_id"] == "IB-DU123"
    assert config.actors[1].config["snapshot_interval_seconds"] == 30
    assert config.strategies[0].config["account_id"] == "IB-DU123"
    assert config.strategies[0].config["instrument_routes"]["SPY.US"] == "SPY.ARCA"
    assert config.actors[0].config["bar_types"][0].endswith("1-DAY-LAST-INTERNAL")
    assert config.actors[0].config["bootstrap_bar_types"][0].endswith("1-DAY-LAST-EXTERNAL")
    assert config.exec_clients["IB"].routing.default is True
    assert str(AccountId(config.strategies[0].config["account_id"])) == "IB-DU123"


def test_builds_patchtst_actor_only_from_complete_production_factors(
    tmp_path: Path,
) -> None:
    catalog_path = _patchtst_project(tmp_path, source_kind="signal_inference")
    config = runner.build_trading_node_config(
        project_root=tmp_path,
        environ={
            "TRADING_MODE": "paper",
            "TWS_ACCOUNT": "DU123",
            "CATALOG_PATH": str(catalog_path),
        },
    )
    assert config.actors[0].actor_path.endswith(":PatchTSTFactorActor")
    assert config.actors[0].config["bootstrap_from_catalog"] is True
    assert config.actors[0].config["stream_data"] is False
    assert config.actors[0].config["allow_evaluation_predictions"] is False


@pytest.mark.parametrize(
    ("source_kind", "batch_size", "message"),
    [
        (None, 1, "no FactorScoreData"),
        ("evaluation_predictions", 1, "requires signal_inference"),
        ("signal_inference", 2, "incomplete"),
    ],
)
def test_patchtst_paper_preflight_rejects_unsafe_factor_catalog(
    tmp_path: Path,
    source_kind: str | None,
    batch_size: int,
    message: str,
) -> None:
    catalog_path = _patchtst_project(
        tmp_path,
        source_kind=source_kind,
        batch_size=batch_size,
    )
    with pytest.raises(ValueError, match=message):
        runner.build_trading_node_config(
            project_root=tmp_path,
            environ={
                "TRADING_MODE": "paper",
                "TWS_ACCOUNT": "DU123",
                "CATALOG_PATH": str(catalog_path),
            },
        )


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
