"""从已准备的运行目录装配 NT 原生 paper TradingNode。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    IB_CLIENTS,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import (
    DataCatalogConfig,
    ImportableActorConfig,
    ImportableStrategyConfig,
    RoutingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from trading_assistant.data.catalog import CatalogRepository, catalog_lock
from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.execution.gateway import ExecutionGatewayStrategy
from trading_assistant.live.config import load_live_settings
from trading_assistant.live.portfolio_snapshot import PortfolioSnapshotActor
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import load_active_strategy
from trading_assistant.strategies.runtime import StrategyRuntimeContext, build_strategy_actor


class BrokerSession:
    """本节点复用 NT 的连接与核对, 两个消费方读取同一会话事实。"""

    def __init__(self, node: TradingNode, client: InteractiveBrokersClient) -> None:
        self.node = node
        self.client = client
        self._reconciled = False
        self._started = False
        self._epoch: int | None = None

    def status(self) -> tuple[bool, bool]:
        connected = bool(self.client.is_ready and self.node.kernel.exec_engine.check_connected())
        # NT 1.230 的重连世代保留在客户端; 仅用它失效旧核对, 不改变适配器状态。
        epoch = self.client._last_disconnection_ns
        if not connected or epoch != self._epoch:
            self._reconciled = False
        if not self._started and self.node.kernel.trader.is_running:
            # TradingNode 仅在启动核对成功后才启动 Trader。
            self._started = True
            self._reconciled = connected and epoch == self._epoch
        return connected, self._reconciled

    async def monitor(self) -> None:
        """断连后重新等待 NT 核对成功; 请求有界且不阻塞事件循环。"""
        while True:
            connected, ready = self.status()
            if connected and self._started and not ready:
                epoch = self.client._last_disconnection_ns
                try:
                    complete = await asyncio.wait_for(
                        self.node.kernel.exec_engine.reconcile_execution_state(timeout_secs=30),
                        timeout=30,
                    )
                except (TimeoutError, RuntimeError):
                    complete = False
                self._epoch = epoch
                self._reconciled = bool(complete and epoch == self.client._last_disconnection_ns)
            await asyncio.sleep(1 if ready else 5)


def build_trading_node_config(
    *,
    project_root: Path,
    environ: Mapping[str, str],
) -> TradingNodeConfig:
    """从 YAML 与环境变量构建 paper-only NT TradingNode 配置。"""
    if environ.get("TRADING_MODE", "paper") != "paper":
        raise ValueError("Only TRADING_MODE=paper is supported")
    tws_account = environ.get("TWS_ACCOUNT", "").strip()
    if not tws_account:
        raise ValueError("TWS_ACCOUNT is required")
    if not tws_account.startswith("DU"):
        raise ValueError("Only an IBKR paper account (DU prefix) is supported")
    data = load_data_config(project_root / "config" / "data.yaml")
    strategy = load_active_strategy(project_root / "config" / "strategies.yaml")
    risk = load_risk_limits(project_root / "config" / "risk.yaml")
    live = load_live_settings(project_root / "config" / "live.yaml")
    instruments = load_instruments(project_root / "config" / "instruments.yaml")
    canonical_ids = tuple(sorted(item.canonical_id for item in instruments))
    live_routes = {item.canonical_id: item.resolved_live_instrument_id for item in instruments}
    native_ids = frozenset(InstrumentId.from_str(value) for value in live_routes.values())
    signal_bar_types = tuple(
        f"{instrument_id}-{data.historical_data.signal_bar_type_suffix}"
        for instrument_id in canonical_ids
    )
    execution_bar_types = {
        instrument_id: (f"{instrument_id}-{data.historical_data.execution_bar_type_suffix}")
        for instrument_id in canonical_ids
    }
    database_url = environ.get(
        "LIVE_DATABASE_URL", f"sqlite:///{project_root / 'runtime' / 'data' / 'live.db'}"
    )
    catalog_path = Path(
        environ.get("CATALOG_PATH", str(project_root / "runtime" / "catalog" / "eodhd")),
    ).resolve()
    account_id = f"IB-{tws_account}"
    signal_scope = f"paper:{tws_account}"
    signal_actor = build_strategy_actor(
        strategy,
        StrategyRuntimeContext(
            instrument_ids=canonical_ids,
            signal_bar_types=signal_bar_types,
            database_url=database_url,
            signal_scope=signal_scope,
            stream_bars=False,
            bootstrap_from_catalog=True,
            catalog_lookback_days=live.catalog_lookback_days,
            publish_after_ns=0,
            allow_evaluation_predictions=False,
            catalog_path=str(catalog_path),
            max_factor_unscorable_fraction=risk.max_factor_unscorable_fraction,
            factor_check_interval_seconds=live.factor_check_interval_seconds,
        ),
    )
    portfolio_actor = ImportableActorConfig(
        actor_path="trading_assistant.live.portfolio_snapshot:PortfolioSnapshotActor",
        config_path=("trading_assistant.live.portfolio_snapshot:PortfolioSnapshotActorConfig"),
        config={
            "account_id": account_id,
            "database_url": database_url,
            "snapshot_interval_seconds": live.portfolio_snapshot_interval_seconds,
            "currency": "USD",
            "broker_account_stale_after_seconds": live.broker_account_stale_after_seconds,
        },
    )
    gateway = ImportableStrategyConfig(
        strategy_path="trading_assistant.execution.gateway:ExecutionGatewayStrategy",
        config_path="trading_assistant.execution.gateway:ExecutionGatewayConfig",
        config={
            "instrument_routes": live_routes,
            "execution_bar_types": execution_bar_types,
            "approval_mode": strategy.settings.approval_mode,
            "database_url": database_url,
            "signal_scope": signal_scope,
            "strategy_capital_usd": risk.strategy_capital_usd,
            "max_order_notional_usd": risk.max_order_notional_usd,
            "max_instrument_weight": risk.max_instrument_weight,
            "max_daily_new_positions": risk.max_daily_new_positions,
            "max_gross_exposure": risk.max_gross_exposure,
            "max_factor_unscorable_fraction": risk.max_factor_unscorable_fraction,
            "max_factor_preserved_price_age_sessions": risk.max_factor_preserved_price_age_sessions,
            "model_release_id": getattr(strategy.settings, "model_release_id", None),
            "approval_poll_interval_seconds": live.approval_poll_interval_seconds,
            "account_id": account_id,
            "bootstrap_from_catalog": True,
            "catalog_lookback_days": live.catalog_lookback_days,
            "broker_account_stale_after_seconds": live.broker_account_stale_after_seconds,
            "corporate_action_path": str(catalog_path.with_name(catalog_path.name + "-actions")),
        },
    )
    execution = InteractiveBrokersExecClientConfig(
        ibg_host=environ.get("IB_HOST", "127.0.0.1"),
        ibg_port=int(environ.get("IB_PORT", "4002")),
        ibg_client_id=int(environ.get("IB_EXEC_CLIENT_ID", "1202")),
        account_id=tws_account,
        request_timeout_secs=data.historical_data.request_timeout_seconds,
        instrument_provider=InteractiveBrokersInstrumentProviderConfig(load_ids=native_ids),
        routing=RoutingConfig(default=True),
    )
    return TradingNodeConfig(
        trader_id="TRADER-001",
        catalogs=[DataCatalogConfig(path=str(catalog_path))],
        actors=[signal_actor, portfolio_actor],
        strategies=[gateway],
        exec_clients={"IB": execution},
        logging=LoggingConfig(log_level=environ.get("LOG_LEVEL", "INFO")),
    )


def validate_live_runtime(*, project_root: Path, environ: Mapping[str, str]) -> None:
    """在连接券商前验证已迁移的运行库与协调锁, 不采集供应商数据。"""
    catalog = (
        Path(environ.get("CATALOG_PATH", str(project_root / "runtime" / "catalog" / "eodhd")))
        .expanduser()
        .resolve()
    )
    if not catalog.is_dir() or not (catalog / ".catalog.lock").is_file():
        raise RuntimeError("Runtime Catalog is not prepared by paper-data-sync")
    with catalog_lock(catalog, exclusive=False):
        pass
    repository = TradingRepository(
        environ.get(
            "LIVE_DATABASE_URL", f"sqlite:///{project_root / 'runtime' / 'data' / 'live.db'}"
        ),
        read_only=True,
    )
    try:
        repository.verify_schema()
    finally:
        repository.close()


def run_live(*, project_root: Path, environ: Mapping[str, str] | None = None) -> None:
    """验证目录后运行 TradingNode 并等待停止信号。"""
    values = dict(os.environ if environ is None else environ)
    if values.get("TRADING_MODE", "paper") != "paper":
        raise ValueError("Only TRADING_MODE=paper is supported")

    validate_live_runtime(project_root=project_root, environ=values)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node: TradingNode | None = None
    monitor: asyncio.Task[None] | None = None
    try:
        node = TradingNode(
            config=build_trading_node_config(project_root=project_root, environ=values),
        )
        for name, original in tuple(node.kernel.catalogs.items()):
            catalog = CatalogRepository(Path(cast(ParquetDataCatalog, original).path)).catalog
            node.kernel.catalogs[name] = catalog
            node.kernel.data_engine.register_catalog(catalog, name)
        node.add_exec_client_factory("IB", InteractiveBrokersLiveExecClientFactory)
        node.build()
        key = (
            values.get("IB_HOST", "127.0.0.1"),
            int(values.get("IB_PORT", "4002")),
            int(values.get("IB_EXEC_CLIENT_ID", "1202")),
        )
        session = BrokerSession(node, IB_CLIENTS[key])
        for component in [*node.kernel.trader.actors(), *node.kernel.trader.strategies()]:
            if isinstance(component, (ExecutionGatewayStrategy, PortfolioSnapshotActor)):
                component.bind_broker_status(session.status)
        monitor = loop.create_task(session.monitor())
        node.run(raise_exception=True)
    finally:
        if monitor is not None:
            monitor.cancel()
            loop.run_until_complete(asyncio.gather(monitor, return_exceptions=True))
        if node is not None:
            node.dispose()
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)
