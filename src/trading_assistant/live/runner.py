"""从已准备的运行目录装配 NT 原生 paper TradingNode。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.client.common import get_venue_order_id
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
    ImportableActorConfig,
    ImportableStrategyConfig,
    RoutingConfig,
    TradingNodeConfig,
)
from nautilus_trader.execution.reports import ExecutionMassStatus
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import AccountId, InstrumentId
from nautilus_trader.model.objects import Currency

from trading_assistant.data.catalog import CatalogBusyError, catalog_lock
from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.execution.gateway import ExecutionGatewayStrategy
from trading_assistant.live.catalog_client import CatalogDataClientConfig, CatalogDataClientFactory
from trading_assistant.live.config import LiveSettings, load_live_settings
from trading_assistant.live.portfolio_snapshot import (
    PortfolioSnapshotActor,
    account_not_ready_reason,
    account_update,
)
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import load_active_strategy
from trading_assistant.strategies.runtime import StrategyRuntimeContext, build_strategy_actor


class BrokerSession:
    """本节点拥有一轮核对任务; 共享 IB 请求只由原生超时或券商回调结束。"""

    def __init__(
        self,
        node: TradingNode,
        client: InteractiveBrokersClient,
        *,
        account_id: AccountId,
        settings: LiveSettings,
    ) -> None:
        self.node = node
        self.client = client
        self._account_id = account_id
        self._settings = settings
        self._reconciled = False
        self._epoch: int | None = None
        self._task: asyncio.Task[bool] | None = None
        self._cycle_epoch: int | None = None
        self._cycle_started_ns = 0
        self._cycle_invalidated = False
        self._report: ExecutionMassStatus | None = None
        self._closed = False
        node.kernel.msgbus.subscribe("reports.execution.*", self._on_report)

    def status(self) -> tuple[bool, bool]:
        """连接事实随时失效; 就绪只能由本轮完整核对置为真。"""
        connected = bool(self.client.is_ready and self.node.kernel.exec_engine.check_connected())
        if self._closed or not connected or self.client._last_disconnection_ns != self._epoch:
            self._reconciled = False
        return connected, self._reconciled

    def _on_report(self, report: object) -> None:
        if (
            isinstance(report, ExecutionMassStatus)
            and report.account_id == self._account_id
            and self._task is not None
            and not self._cycle_invalidated
            and self._cycle_epoch == self.client._last_disconnection_ns
            and report.ts_init >= self._cycle_started_ns
        ):
            self._report = report

    def invalidate(self, reason: str) -> None:
        """执行一致性失效后, 只有下一轮完整核对才能重新开放门禁。"""
        self._reconciled = False
        self._cycle_invalidated = True
        self.node.kernel.logger.warning(f"Broker reconciliation invalidated: {reason}")
        # 执行异常立即落盘未就绪状态, 网页不必等待下一次定时采样。
        for actor in self.node.kernel.trader.actors():
            if isinstance(actor, PortfolioSnapshotActor):
                actor._capture_snapshot(None)

    async def _reconcile(self) -> bool:
        # 复用原有账户订阅, 获取真实来源回报而非刷新本地快照时间。
        self.client.subscribe_account_summary()
        complete = await self.node.kernel.exec_engine.reconcile_execution_state(
            timeout_secs=self._settings.broker_reconciliation_timeout_seconds
        )
        report = self._report
        if not complete or report is None:
            return False
        account = self.node.kernel.cache.account(self._account_id)
        update = account_update(account, Currency.from_str("USD"))
        if (
            account_not_ready_reason(
                account,
                now_ns=self.node.kernel.clock.timestamp_ns(),
                stale_after_seconds=self._settings.broker_account_stale_after_seconds,
                broker_connected=self.status()[0],
                reconciliation_complete=True,
            )
            is not None
            or update is None
        ):
            return False
        if self._cycle_epoch is not None and update.ts_event < self._cycle_epoch:
            return False

        # NT 1.230 部分报告生成器会吞掉转换异常并返回部分列表。
        # 用同一个原生客户端的完整结束回报核验覆盖, 不建立第二份持仓状态。
        positions, orders, executions = await asyncio.gather(
            self.client.get_positions(self._account_id.get_id()),
            self.client.get_open_orders(self._account_id.get_id()),
            self.client.get_executions(self._account_id.get_id()),
            return_exceptions=True,
        )
        if (
            positions is None
            or isinstance(positions, BaseException)
            or orders is None
            or isinstance(orders, BaseException)
            or executions is None
            or isinstance(executions, BaseException)
        ):
            return False
        provider = self.client._instrument_provider
        reported: dict[InstrumentId, Decimal] = {}
        for instrument_id, reports in report.position_reports.items():
            if any(item.account_id != self._account_id for item in reports):
                return False
            reported[instrument_id] = sum((item.signed_decimal_qty for item in reports), Decimal(0))
        observed: dict[InstrumentId, Decimal] = {}
        for position in positions:
            instrument_id = provider.contract_id_to_instrument_id.get(position.contract.conId)
            if instrument_id is None or position.account_id != self._account_id.get_id():
                return False
            observed[instrument_id] = observed.get(instrument_id, Decimal(0)) + position.quantity
        cached: dict[InstrumentId, Decimal] = {}
        for position in self.node.kernel.cache.positions_open(account_id=self._account_id):
            if position.instrument_id in cached or position.signed_decimal_qty() <= 0:
                return False
            cached[position.instrument_id] = (
                cached.get(position.instrument_id, Decimal(0)) + position.signed_decimal_qty()
            )
        instruments = reported.keys() | observed.keys() | cached.keys()
        if any(
            reported.get(item, Decimal(0)) != observed.get(item, Decimal(0))
            or reported.get(item, Decimal(0)) != cached.get(item, Decimal(0))
            for item in instruments
        ):
            return False
        if any(
            get_venue_order_id(order.orderId, order.permId) not in report.order_reports
            for order in orders
        ):
            return False
        if any(item.account_id != self._account_id for item in report.order_reports.values()):
            return False
        fills = {
            str(fill.trade_id): fill for values in report.fill_reports.values() for fill in values
        }
        for detail in executions:
            execution = detail.get("execution")
            if (
                execution is None
                or detail.get("contract") is None
                or detail.get("commission_report") is None
                or execution.acctNumber != self._account_id.get_id()
            ):
                return False
            fill = fills.get(execution.execId)
            if (
                fill is None
                or fill.account_id != self._account_id
                or fill.last_qty.as_decimal() != execution.shares
            ):
                return False
        return (
            account_not_ready_reason(
                account,
                now_ns=self.node.kernel.clock.timestamp_ns(),
                stale_after_seconds=self._settings.broker_account_stale_after_seconds,
                broker_connected=self.status()[0],
                reconciliation_complete=True,
            )
            is None
        )

    async def monitor(self) -> None:
        """周期等待不取消核对; 超期结果失效, 收尾后才允许下一轮。"""
        loop = asyncio.get_running_loop()
        next_attempt = 0.0
        deadline = 0.0
        expired = False
        try:
            while not self._closed:
                connected, ready = self.status()
                if self._task is not None:
                    if loop.time() >= deadline and not expired:
                        expired = True
                        self._reconciled = False
                        self.node.kernel.logger.warning("Broker reconciliation deadline exceeded")
                    if self._task.done():
                        try:
                            complete = self._task.result()
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            complete = False
                            self.node.kernel.logger.warning(
                                f"Broker reconciliation failed: {type(exc).__name__}"
                            )
                        self._reconciled = bool(
                            complete
                            and not expired
                            and not self._cycle_invalidated
                            and connected
                            and self._cycle_epoch == self.client._last_disconnection_ns
                        )
                        self._epoch = self._cycle_epoch
                        self._task = None
                        next_attempt = (
                            loop.time()
                            + self._settings.broker_reconciliation_retry_interval_seconds
                        )
                elif connected and not ready and loop.time() >= next_attempt:
                    # Trader 先以未就绪门禁启动; 首次与重连核对均由本会话串行管理。
                    if self.node.kernel.trader.is_running:
                        self._cycle_epoch = self.client._last_disconnection_ns
                        self._cycle_started_ns = self.node.kernel.clock.timestamp_ns()
                        self._report = None
                        self._cycle_invalidated = False
                        expired = False
                        deadline = (
                            loop.time() + self._settings.broker_reconciliation_timeout_seconds
                        )
                        self._task = loop.create_task(
                            self._reconcile(), name="broker-reconciliation"
                        )
                if self._task is not None:
                    await asyncio.wait(
                        {self._task},
                        timeout=1.0 if expired else min(1.0, max(0.01, deadline - loop.time())),
                    )
                else:
                    await asyncio.sleep(
                        min(1.0, self._settings.broker_reconciliation_retry_interval_seconds)
                    )
        finally:
            self._reconciled = False

    async def close(self) -> None:
        """停止会话后等待原生请求收尾, 仅退出进程时取消超出完整期限的任务。"""
        if self._closed and self._task is None:
            return
        self._closed = True
        self._reconciled = False
        self.node.kernel.msgbus.unsubscribe("reports.execution.*", self._on_report)
        if self._task is not None:
            await asyncio.wait(
                {self._task}, timeout=self._settings.broker_reconciliation_timeout_seconds
            )
            if not self._task.done():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


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
            # 仅内部关联允许绑定恢复的实际 PositionId; 仍限制唯一多头持仓。
            "oms_type": "HEDGING",
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
        request_timeout_secs=live.broker_request_timeout_seconds,
        instrument_provider=InteractiveBrokersInstrumentProviderConfig(load_ids=native_ids),
        routing=RoutingConfig(default=True),
    )
    return TradingNodeConfig(
        trader_id="TRADER-001",
        data_clients={
            "CATALOG": CatalogDataClientConfig(
                catalog_path=str(catalog_path),
                request_timeout_seconds=live.catalog_request_timeout_seconds,
            ),
        },
        timeout_reconciliation=live.broker_reconciliation_timeout_seconds,
        exec_engine=LiveExecEngineConfig(reconciliation=False),
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
    try:
        with catalog_lock(catalog, exclusive=False):
            pass
    except CatalogBusyError:
        # 活跃发布由异步客户端下一轮处理, 不将暂时持锁误判为目录损坏。
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
    session: BrokerSession | None = None
    try:
        node = TradingNode(
            config=build_trading_node_config(project_root=project_root, environ=values),
        )
        node.add_data_client_factory("CATALOG", CatalogDataClientFactory)
        node.add_exec_client_factory("IB", InteractiveBrokersLiveExecClientFactory)
        node.build()
        key = (
            values.get("IB_HOST", "127.0.0.1"),
            int(values.get("IB_PORT", "4002")),
            int(values.get("IB_EXEC_CLIENT_ID", "1202")),
        )
        session = BrokerSession(
            node,
            IB_CLIENTS[key],
            account_id=AccountId(f"IB-{values['TWS_ACCOUNT']}"),
            settings=load_live_settings(project_root / "config" / "live.yaml"),
        )
        for component in [*node.kernel.trader.actors(), *node.kernel.trader.strategies()]:
            if isinstance(component, ExecutionGatewayStrategy):
                component.bind_broker_status(session.status, invalidate=session.invalidate)
            elif isinstance(component, PortfolioSnapshotActor):
                component.bind_broker_status(session.status)
        monitor = loop.create_task(session.monitor())
        node.run(raise_exception=True)
    finally:
        if monitor is not None:
            monitor.cancel()
            loop.run_until_complete(asyncio.gather(monitor, return_exceptions=True))
        if session is not None:
            loop.run_until_complete(session.close())
        if node is not None:
            node.dispose()
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)
