"""M1 同步后装配并运行 NT 原生 TradingNode。"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
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

from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.live.config import load_live_settings
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.strategies.config import load_dual_momentum_settings

LOGGER = logging.getLogger(__name__)


def build_trading_node_config(
    *,
    project_root: Path,
    environ: Mapping[str, str],
) -> TradingNodeConfig:
    """从 YAML 与环境变量构建 paper-only NT TradingNode 配置。"""
    if environ.get("TRADING_MODE", "paper") != "paper":
        raise ValueError("M3 only permits TRADING_MODE=paper")
    tws_account = environ.get("TWS_ACCOUNT", "").strip()
    if not tws_account:
        raise ValueError("TWS_ACCOUNT is required")
    data = load_data_config(project_root / "config" / "data.yaml")
    strategy = load_dual_momentum_settings(project_root / "config" / "strategies.yaml")
    risk = load_risk_limits(project_root / "config" / "risk.yaml")
    live = load_live_settings(project_root / "config" / "live.yaml")
    instruments = load_instruments(project_root / "config" / "instruments.yaml")
    instrument_ids = tuple(sorted(item.instrument_id for item in instruments))
    native_ids = frozenset(InstrumentId.from_str(value) for value in instrument_ids)
    bar_types = tuple(
        f"{instrument_id}-{data.historical_data.bar_type_suffix}"
        for instrument_id in instrument_ids
    )
    database_url = environ.get("DATABASE_URL", "sqlite:///./data/trading_assistant.db")
    catalog_path = Path(environ.get("CATALOG_PATH", str(project_root / "catalog"))).resolve()
    account_id = f"IB-{tws_account}"
    signal_actor = ImportableActorConfig(
        actor_path="trading_assistant.strategies.dual_momentum:DualMomentumActor",
        config_path="trading_assistant.strategies.dual_momentum:DualMomentumActorConfig",
        config={
            "bar_types": list(bar_types),
            "instrument_ids": list(instrument_ids),
            "lookback_months": strategy.lookback_months,
            "top_n": strategy.top_n,
            "fallback_instrument": strategy.fallback_instrument,
            "signal_expiry_hours": strategy.signal_expiry_hours,
            "database_url": database_url,
            "signal_scope": f"paper:{tws_account}",
            "stream_bars": False,
            "bootstrap_from_catalog": True,
            "catalog_lookback_days": live.catalog_lookback_days,
        },
    )
    portfolio_actor = ImportableActorConfig(
        actor_path="trading_assistant.live.portfolio_snapshot:PortfolioSnapshotActor",
        config_path=("trading_assistant.live.portfolio_snapshot:PortfolioSnapshotActorConfig"),
        config={
            "account_id": account_id,
            "database_url": database_url,
            "snapshot_interval_seconds": live.portfolio_snapshot_interval_seconds,
            "currency": "USD",
        },
    )
    gateway = ImportableStrategyConfig(
        strategy_path="trading_assistant.execution.gateway:ExecutionGatewayStrategy",
        config_path="trading_assistant.execution.gateway:ExecutionGatewayConfig",
        config={
            "instrument_ids": list(instrument_ids),
            "bar_type_suffix": data.historical_data.bar_type_suffix,
            "approval_mode": strategy.approval_mode,
            "database_url": database_url,
            "strategy_capital_usd": risk.strategy_capital_usd,
            "max_order_notional_usd": risk.max_order_notional_usd,
            "max_instrument_weight": risk.max_instrument_weight,
            "max_daily_new_positions": risk.max_daily_new_positions,
            "max_gross_exposure": risk.max_gross_exposure,
            "approval_poll_interval_seconds": live.approval_poll_interval_seconds,
            "account_id": account_id,
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


def sync_live_catalog(*, project_root: Path, environ: Mapping[str, str]) -> None:
    """在隔离进程中调用 M1 CLI, 避免重复初始化 NT 全局日志器。"""
    command = [sys.executable, str(project_root / "scripts" / "fetch_data.py")]
    try:
        subprocess.run(  # noqa: S603 -- 命令仅由解释器与项目内固定脚本组成。
            command,
            cwd=project_root,
            env=dict(environ),
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Catalog sync process failed") from exc
    LOGGER.info("Catalog sync process completed")


def run_live(*, project_root: Path, environ: Mapping[str, str] | None = None) -> None:
    """同步 Catalog 后运行 TradingNode 并等待停止信号。"""
    values = dict(os.environ if environ is None else environ)
    if values.get("TRADING_MODE", "paper") != "paper":
        raise ValueError("M3 only permits TRADING_MODE=paper")

    # 历史客户端和 TradingNode 都会初始化 NT 的进程级日志器, 必须进程隔离。
    sync_live_catalog(project_root=project_root, environ=values)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node: TradingNode | None = None
    try:
        node = TradingNode(
            config=build_trading_node_config(project_root=project_root, environ=values),
        )
        node.add_exec_client_factory("IB", InteractiveBrokersLiveExecClientFactory)
        node.build()
        node.run(raise_exception=True)
    finally:
        if node is not None:
            node.dispose()
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)
