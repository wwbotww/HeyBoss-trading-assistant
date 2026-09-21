"""TradingNode paper-only 离线装配测试。"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.identifiers import AccountId

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.data.market_calendar import CALENDAR_VERSION
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
    values["strategies"]["patchtst_e3"]["parameters"]["model_release_id"] = "b" * 64
    strategy_path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    catalog_path = tmp_path / "catalog"
    if source_kind is not None:
        score = FactorScoreData(
            calendar_version=CALENDAR_VERSION,
            canonical_id="SPY.US",
            security_id="eodhd:isin:SPY",
            asof_date="2025-01-02",
            score=1.0,
            eligible=True,
            batch_id="delivery:2025-01-02",
            batch_size=batch_size,
            delivery_id="d" * 64,
            model_release_id="b" * 64,
            source_kind=source_kind,
            ts_event=1,
            ts_init=1,
        )
        CatalogRepository(catalog_path).catalog.write_data([CustomData(FACTOR_DATA_TYPE, score)])
    return catalog_path


def test_builds_paper_trading_node_from_native_components(tmp_path: Path) -> None:
    catalog_path = _patchtst_project(tmp_path, source_kind="signal_inference", batch_size=1)
    config = runner.build_trading_node_config(
        project_root=tmp_path,
        environ={
            "TRADING_MODE": "paper",
            "TWS_ACCOUNT": "DU123",
            "CATALOG_PATH": str(catalog_path),
        },
    )
    assert tuple(config.exec_clients) == ("IB",)
    assert len(config.actors) == 2
    assert config.actors[0].config["strategy_name"] == "patchtst_e3"
    assert config.actors[0].config["bootstrap_from_catalog"] is True
    assert config.actors[0].config["stream_data"] is False
    assert config.actors[1].config["account_id"] == "IB-DU123"
    assert config.actors[1].config["snapshot_interval_seconds"] == 30
    assert config.strategies[0].config["account_id"] == "IB-DU123"
    assert config.strategies[0].config["signal_scope"] == "paper:DU123"
    assert config.strategies[0].config["bootstrap_from_catalog"] is True
    assert config.strategies[0].config["catalog_lookback_days"] == 2200
    assert config.strategies[0].config["instrument_routes"]["AAPL.US"] == "AAPL.NASDAQ"
    assert (
        config.strategies[0]
        .config["execution_bar_types"]["AAPL.US"]
        .endswith("1-DAY-LAST-EXTERNAL")
    )
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
    ("source_kind", "batch_size"),
    [
        (None, 1),
        ("evaluation_predictions", 1),
        ("signal_inference", 2),
    ],
)
def test_patchtst_paper_starts_monitoring_even_without_usable_factor_batch(
    tmp_path: Path,
    source_kind: str | None,
    batch_size: int,
) -> None:
    catalog_path = _patchtst_project(
        tmp_path,
        source_kind=source_kind,
        batch_size=batch_size,
    )
    config = runner.build_trading_node_config(
        project_root=tmp_path,
        environ={
            "TRADING_MODE": "paper",
            "TWS_ACCOUNT": "DU123",
            "CATALOG_PATH": str(catalog_path),
        },
    )
    actor = config.actors[0].config
    assert actor["bootstrap_from_catalog"] is True
    assert actor["allow_evaluation_predictions"] is False
    assert actor["factor_check_interval_seconds"] == 60
    assert actor["model_release_id"] == "b" * 64


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


def test_live_runtime_requires_prepared_catalog_and_migrated_database(tmp_path: Path) -> None:
    from trading_assistant.storage.repository import TradingRepository

    catalog = tmp_path / "catalog"
    environ = {
        "CATALOG_PATH": str(catalog),
        "LIVE_DATABASE_URL": f"sqlite:///{tmp_path / 'live.db'}",
    }
    with pytest.raises(RuntimeError, match="not prepared"):
        runner.validate_live_runtime(project_root=tmp_path, environ=environ)
    CatalogRepository(catalog)
    repository = TradingRepository(environ["LIVE_DATABASE_URL"])
    repository.create_schema()
    repository.close()
    runner.validate_live_runtime(project_root=tmp_path, environ=environ)


def test_run_live_builds_runs_and_disposes_node(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_sync(**_: object) -> None:
        calls.append("validate")

    class FakeNode:
        def __init__(self, config: object) -> None:
            assert config == "config"
            self.kernel = SimpleNamespace(
                catalogs={}, trader=SimpleNamespace(actors=lambda: [], strategies=lambda: [])
            )

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

    monkeypatch.setattr(runner, "validate_live_runtime", fake_sync)
    monkeypatch.setattr(runner, "build_trading_node_config", lambda **_: "config")
    monkeypatch.setattr(runner, "TradingNode", cast(Any, FakeNode))
    monkeypatch.setitem(runner.IB_CLIENTS, ("127.0.0.1", 4002, 1202), cast(Any, object()))
    runner.run_live(
        project_root=Path.cwd(),
        environ={"TRADING_MODE": "paper", "TWS_ACCOUNT": "DU123"},
    )
    assert calls == ["validate", "factory", "build", "run", "dispose"]


def test_run_live_does_not_create_node_when_runtime_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_sync(**_: object) -> None:
        raise RuntimeError("sync failed")

    monkeypatch.setattr(runner, "validate_live_runtime", fake_sync)
    with pytest.raises(RuntimeError, match="sync failed"):
        runner.run_live(
            project_root=Path.cwd(),
            environ={"TRADING_MODE": "paper", "TWS_ACCOUNT": "DU123"},
        )


def test_run_live_rejects_live_before_validation() -> None:
    with pytest.raises(ValueError, match="paper"):
        runner.run_live(
            project_root=Path.cwd(),
            environ={"TRADING_MODE": "live", "TWS_ACCOUNT": "U123"},
        )


def test_session_invalidates_reconciliation_on_transport_reconnect() -> None:
    import asyncio

    async def scenario() -> None:
        received = asyncio.Event()

        async def reconcile(*, timeout_secs: float) -> bool:
            assert timeout_secs == 30
            received.set()
            return True

        client = SimpleNamespace(is_ready=True, _last_disconnection_ns=None)
        node = SimpleNamespace(
            kernel=SimpleNamespace(
                trader=SimpleNamespace(is_running=True),
                exec_engine=SimpleNamespace(
                    check_connected=lambda: True, reconcile_execution_state=reconcile
                ),
            )
        )
        session = runner.BrokerSession(node, client)
        assert session.status() == (True, True)
        client.is_ready = False
        client._last_disconnection_ns = 10
        assert session.status() == (False, False)
        client.is_ready = True
        assert session.status() == (True, False)
        task = asyncio.create_task(session.monitor())
        await asyncio.wait_for(received.wait(), timeout=1)
        await asyncio.sleep(0)
        assert session.status() == (True, True)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
