"""用真实 NT IB 请求和执行核对组件验证恢复, 所有券商回调均在进程内模拟。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import msgspec
import pytest
from nautilus_trader.accounting.accounts.margin import MarginAccount
from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.client.common import IBPosition
from nautilus_trader.adapters.interactive_brokers.common import IBContract, IBContractDetails
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.execution import InteractiveBrokersExecutionClient
from nautilus_trader.adapters.interactive_brokers.providers import (
    InteractiveBrokersInstrumentProvider,
)
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common import Environment
from nautilus_trader.common.component import LiveClock, Logger, MessageBus
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.execution.reports import ExecutionMassStatus
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import LiquiditySide, OrderSide
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import AccountId, PositionId, TradeId, TraderId, VenueOrderId
from nautilus_trader.model.objects import Currency, Money
from nautilus_trader.model.orders import Order
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs

from tests.data.helpers import make_bar
from trading_assistant.data.market_calendar import CALENDAR_VERSION
from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, FactorContext, TradeSignalEvent
from trading_assistant.execution.gateway import ExecutionGatewayConfig, ExecutionGatewayStrategy
from trading_assistant.live.config import load_live_settings
from trading_assistant.live.portfolio_snapshot import (
    PortfolioSnapshotActor,
    PortfolioSnapshotActorConfig,
)
from trading_assistant.live.runner import BrokerSession, build_trading_node_config

ACCOUNT = AccountId("IB-DUTEST")


class _Broker:
    """只替换套接字发送口, 请求表、结束回调、报告生成和 Cache 核对使用 NT 原件。"""

    def __init__(
        self, *, quantity: int = 0, cycle_deadline: float = 1, node: TradingNode | None = None
    ) -> None:
        self.loop = asyncio.get_running_loop()
        self.clock = LiveClock() if node is None else node.kernel.clock
        self.cache = Cache() if node is None else node.kernel.cache
        self.bus = (
            MessageBus(trader_id=TraderId("TESTER-BROKER"), clock=self.clock)
            if node is None
            else node.kernel.msgbus
        )
        self.client = InteractiveBrokersClient(
            loop=self.loop, msgbus=self.bus, cache=self.cache, clock=self.clock
        )
        self.client._request_timeout_secs = 0.15
        self.client._is_client_ready.set()
        self.delay = 0.0
        self.complete_positions = True
        self.complete_executions = True
        self.refresh_account = True
        self.positions_calls = 0
        self.contract = IBContract(conId=123, symbol="AAPL", secType="STK", currency="USD")
        self.positions = (
            [IBPosition(ACCOUNT.get_id(), self.contract, Decimal(quantity), 100.0)]
            if quantity
            else []
        )
        self.executions: list[dict[str, Any]] = []
        self.callbacks: list[asyncio.TimerHandle] = []
        self.client._eclient = SimpleNamespace(
            reqPositions=self.request_positions,
            reqOpenOrders=lambda: self.finish("OpenOrders", []),
            reqExecutions=lambda **_: self.request_executions(),
            reqAccountSummary=lambda **_: self.account_summary(),
            cancelAccountSummary=lambda _: None,
        )
        provider = InteractiveBrokersInstrumentProvider(
            self.client, self.clock, InteractiveBrokersInstrumentProviderConfig()
        )
        self.instrument = TestInstrumentProvider.equity(venue="NASDAQ")
        provider.add(self.instrument)
        provider.contract_id_to_instrument_id[123] = self.instrument.id
        provider.contract_details[self.instrument.id] = IBContractDetails(
            contract=self.contract, priceMagnifier=1
        )
        provider.contract[self.instrument.id] = self.contract
        self.client._instrument_provider = provider
        self.cache.add_instrument(self.instrument)
        self.account_summary()
        self.engine = (
            node.kernel.exec_engine
            if node is not None
            else LiveExecutionEngine(
                loop=self.loop,
                msgbus=self.bus,
                cache=self.cache,
                clock=self.clock,
                config=LiveExecEngineConfig(inflight_check_interval_ms=0),
            )
        )
        self.execution = InteractiveBrokersExecutionClient(
            loop=self.loop,
            client=self.client,
            account_id=ACCOUNT,
            msgbus=self.bus,
            cache=self.cache,
            clock=self.clock,
            instrument_provider=provider,
            config=InteractiveBrokersExecClientConfig(account_id=ACCOUNT.get_id()),
        )
        self.execution._set_connected(True)
        self.engine.register_client(self.execution)
        node = node or SimpleNamespace(
            kernel=SimpleNamespace(
                clock=self.clock,
                cache=self.cache,
                msgbus=self.bus,
                exec_engine=self.engine,
                trader=SimpleNamespace(is_running=True, actors=lambda: []),
                logger=Logger("BrokerTest"),
            )
        )
        settings = replace(
            load_live_settings(Path("config/live.yaml")),
            broker_reconciliation_timeout_seconds=cycle_deadline,
            broker_reconciliation_retry_interval_seconds=0.01,
        )
        self.session = BrokerSession(node, self.client, account_id=ACCOUNT, settings=settings)

    def account_summary(self) -> None:
        if not self.refresh_account:
            return
        values = AccountState.to_dict(TestEventStubs.margin_account_state(ACCOUNT))
        values["ts_event"] = values["ts_init"] = self.clock.timestamp_ns()
        event = AccountState.from_dict(values)
        account = self.cache.account(ACCOUNT)
        if account is None:
            self.cache.add_account(MarginAccount(event, calculate_account_state=False))
        else:
            account.apply(event)

    def finish(self, name: str, rows: list[Any]) -> None:
        request = self.client._requests.get(name=name)
        assert request is not None

        def callback() -> None:
            request.result.extend(rows)
            self.client._end_request(request.req_id)

        self.callbacks.append(self.loop.call_later(self.delay, callback))

    def request_positions(self) -> None:
        self.positions_calls += 1
        if self.complete_positions:
            self.finish("OpenPositions", list(self.positions))

    def request_executions(self) -> None:
        if self.complete_executions:
            self.finish(f"Executions-{ACCOUNT.get_id()}", list(self.executions))

    async def wait_ready(self) -> None:
        async with asyncio.timeout(3):
            while not self.session.status()[1]:  # noqa: ASYNC110 -- NT 状态没有完成事件。
                await asyncio.sleep(0.005)


@asynccontextmanager
async def _broker(*, quantity: int = 0, cycle_deadline: float = 1) -> AsyncIterator[_Broker]:
    broker = _Broker(quantity=quantity, cycle_deadline=cycle_deadline)
    broker.engine.start()
    try:
        yield broker
    finally:
        await broker.session.close()
        broker.engine.stop()
        await asyncio.gather(
            broker.engine.get_cmd_queue_task(),
            broker.engine.get_evt_queue_task(),
            return_exceptions=True,
        )
        for callback in broker.callbacks:
            callback.cancel()
        assert broker.session._task is None


def test_outer_cancellation_poisoning_reproduces_with_native_ib_future() -> None:
    async def run() -> None:
        async with _broker() as broker:
            broker.complete_positions = False
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(broker.client.get_positions(ACCOUNT.get_id()), 0.01)
            request = broker.client._requests.get(name="OpenPositions")
            assert request is not None
            assert request.future.cancelled()
            with pytest.raises(asyncio.CancelledError):
                await broker.client.get_positions(ACCOUNT.get_id())
            # 原生 positionEnd 可结束残留请求; 正式修复避免制造这种取消。
            await broker.client.process_position_end()
            broker.complete_positions = True
            assert await broker.client.get_positions(ACCOUNT.get_id()) == []
            assert not broker.client._requests.get_futures()

    asyncio.run(run())


@pytest.mark.parametrize("quantity", [0, 9])
def test_native_reconciliation_restores_positions_and_session_readiness(quantity: int) -> None:
    async def run() -> None:
        async with _broker(quantity=quantity) as broker:
            assert broker.session.status() == (True, False)
            monitor = asyncio.create_task(broker.session.monitor())
            try:
                async with asyncio.timeout(3):
                    while not broker.session.status()[1]:  # noqa: ASYNC110 -- NT 未暴露完成事件, 有界观察其原生状态。
                        await asyncio.sleep(0.01)
                positions = broker.cache.positions_open(account_id=ACCOUNT)
                assert sum(position.signed_decimal_qty() for position in positions) == quantity
                assert not broker.client._requests.get_futures()
                broker.client._is_client_ready.clear()
                broker.client._last_disconnection_ns = broker.clock.timestamp_ns()
                assert broker.session.status() == (False, False)
                broker.client._is_client_ready.set()
                async with asyncio.timeout(3):
                    while not broker.session.status()[1]:  # noqa: ASYNC110 -- NT 未暴露完成事件, 有界观察其原生状态。
                        await asyncio.sleep(0.01)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

    asyncio.run(run())


def test_monitor_deadline_does_not_cancel_shared_future_and_next_cycle_recovers() -> None:
    async def run() -> None:
        async with _broker(cycle_deadline=0.03) as broker:
            broker.delay = 0.07
            monitor = asyncio.create_task(broker.session.monitor())
            try:
                await asyncio.sleep(0.05)
                futures = broker.client._requests.get_futures()
                assert futures
                assert not any(future.cancelled() for future in futures)
                assert broker.session.status() == (True, False)
                task = broker.session._task
                assert task is not None
                broker.delay = 0
                await asyncio.sleep(0.03)
                assert broker.session.status() == (True, False)
                async with asyncio.timeout(3):
                    while not broker.session.status()[1]:  # noqa: ASYNC110 -- NT 未暴露完成事件, 有界观察其原生状态。
                        await asyncio.sleep(0.005)
                assert task.done()
                assert not task.cancelled()
                assert not broker.client._requests.get_futures()
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["positions", "executions", "account", "fill_detail"])
def test_incomplete_native_reports_are_not_confirmed_empty_and_can_recover(missing: str) -> None:
    async def run() -> None:
        async with _broker() as broker:
            broker.complete_positions = missing != "positions"
            broker.complete_executions = missing != "executions"
            if missing == "fill_detail":
                broker.executions = [
                    {
                        "contract": broker.contract,
                        "execution": SimpleNamespace(
                            acctNumber=ACCOUNT.get_id(),
                            execId="missing-commission",
                            shares=Decimal(1),
                        ),
                    }
                ]
            if missing == "account":
                broker.refresh_account = False
                values = AccountState.to_dict(TestEventStubs.margin_account_state(ACCOUNT))
                values["ts_event"] = values["ts_init"] = (
                    broker.clock.timestamp_ns() - 301_000_000_000
                )
                broker.cache.account(ACCOUNT).apply(AccountState.from_dict(values))
            monitor = asyncio.create_task(broker.session.monitor())
            try:
                await asyncio.sleep(0.4)
                assert broker.session.status() == (True, False)
                broker.complete_positions = broker.complete_executions = broker.refresh_account = (
                    True
                )
                broker.executions = []
                await broker.wait_ready()
                assert broker.cache.positions_open(account_id=ACCOUNT) == []
                assert not broker.client._requests.get_futures()
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("cause", ["disconnect", "execution_mismatch"])
def test_previous_connection_success_cannot_restore_new_connection_readiness(cause: str) -> None:
    async def run() -> None:
        async with _broker() as broker:
            broker.delay = 0.08
            monitor = asyncio.create_task(broker.session.monitor())
            try:
                await asyncio.sleep(0.02)
                old_task = broker.session._task
                assert old_task is not None
                if cause == "disconnect":
                    broker.client._last_disconnection_ns = broker.clock.timestamp_ns()
                    broker.client._is_client_ready.clear()
                    assert broker.session.status() == (False, False)
                    broker.client._is_client_ready.set()
                else:
                    broker.session.invalidate("Execution position mismatch")
                    assert broker.session.status() == (True, False)
                await asyncio.shield(old_task)
                await asyncio.sleep(0.005)
                assert broker.session.status() == (True, False)
                broker.delay = 0
                await broker.wait_ready()
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

    asyncio.run(run())


def test_monitor_stop_preserves_inflight_request_until_native_completion() -> None:
    async def run() -> None:
        async with _broker() as broker:
            broker.delay = 0.04
            monitor = asyncio.create_task(broker.session.monitor())
            await asyncio.sleep(0.01)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            assert not any(future.cancelled() for future in broker.client._requests.get_futures())
            await broker.session.close()
            assert broker.session.status() == (True, False)
            assert not broker.client._requests.get_futures()

    asyncio.run(run())


@pytest.mark.parametrize(
    "scenario",
    ["increase", "reduce", "rotate", "mismatch", "netting_mismatch", "unowned", "netting_rejected"],
)
def test_native_kernel_starts_gated_and_retries_first_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    async def run() -> None:
        production = build_trading_node_config(
            project_root=Path.cwd(), environ={"TWS_ACCOUNT": ACCOUNT.get_id()}
        )
        # 保留正式核对开关, 使用相同异步内核的 SANDBOX 环境且不重复初始化全局日志。
        # 移除网络工厂和生产路径; 驱动真实 Kernel/Trader 启停。
        config = msgspec.structs.replace(
            production,
            environment=Environment.SANDBOX,
            trader_id="TESTER-BROKER",
            data_clients={},
            exec_clients={},
            actors=[],
            strategies=[],
            exec_engine=msgspec.structs.replace(
                production.exec_engine, inflight_check_interval_ms=0
            ),
            timeout_post_stop=0.01,
            timeout_disconnection=1,
            logging=LoggingConfig(bypass_logging=True),
        )
        node = TradingNode(config=config, loop=asyncio.get_running_loop())
        broker = _Broker(quantity=9, node=node)
        broker.complete_positions = False
        mismatch = scenario in {"mismatch", "netting_mismatch"}
        rotate = scenario == "rotate" or mismatch
        symbols = ("AAPL", "MSFT", "GOOG") if rotate else ("AAPL",)
        for index, symbol in enumerate(symbols[1:], 124):
            instrument = TestInstrumentProvider.equity(symbol, "NASDAQ")
            contract = IBContract(conId=index, symbol=symbol, secType="STK", currency="USD")
            provider = broker.client._instrument_provider
            provider.add(instrument)
            provider.contract_id_to_instrument_id[index] = instrument.id
            provider.contract_details[instrument.id] = IBContractDetails(
                contract=contract, priceMagnifier=1
            )
            provider.contract[instrument.id] = contract
            broker.cache.add_instrument(instrument)
            if symbol == "MSFT":
                broker.positions.append(IBPosition(ACCOUNT.get_id(), contract, Decimal(5), 100.0))

        async def connect_transport() -> None:
            broker.execution._set_connected(True)

        # 仅替换连接握手, 核对仍经过原生请求/Future/报告/Cache。
        monkeypatch.setattr(broker.execution, "_connect", connect_transport)
        database_url = f"sqlite:///{tmp_path / 'startup.db'}"
        gateway = ExecutionGatewayStrategy(
            ExecutionGatewayConfig(
                oms_type=(
                    "NETTING"
                    if scenario.startswith("netting_")
                    else production.strategies[0].config["oms_type"]
                ),
                instrument_routes={f"{symbol}.US": f"{symbol}.NASDAQ" for symbol in symbols},
                execution_bar_types={
                    f"{symbol}.US": f"{symbol}.US-1-DAY-LAST-EXTERNAL" for symbol in symbols
                },
                approval_mode="auto",
                database_url=database_url,
                account_id=str(ACCOUNT),
                strategy_capital_usd=10000,
                max_order_notional_usd=10000,
                max_instrument_weight=0.25,
                max_daily_new_positions=3,
                max_gross_exposure=0.8,
                broker_account_stale_after_seconds=300,
            )
        )
        snapshot = PortfolioSnapshotActor(
            PortfolioSnapshotActorConfig(account_id=str(ACCOUNT), database_url=database_url)
        )
        node.trader.add_strategy(gateway)
        node.trader.add_actor(snapshot)
        gateway.bind_broker_status(broker.session.status, invalidate=broker.session.invalidate)
        snapshot.bind_broker_status(broker.session.status)
        price_date = broker.clock.utc_now().date() - timedelta(days=1)
        for symbol in symbols:
            broker.cache.add_bar(make_bar(price_date, instrument_id=f"{symbol}.US", close=100))
        # 日历和交付校验由对应集成用例覆盖, 本例保留因子卖后重算及真实执行组件。
        monkeypatch.setattr(gateway, "_factor_rejection", lambda *_, **__: None)
        submitted: list[Order] = []

        async def submit_transport(command: SubmitOrder) -> None:
            order = command.order
            submitted.append(order)
            broker.execution.generate_order_submitted(
                order.strategy_id,
                order.instrument_id,
                order.client_order_id,
                broker.clock.timestamp_ns(),
            )
            broker.execution.generate_order_accepted(
                order.strategy_id,
                order.instrument_id,
                order.client_order_id,
                VenueOrderId(f"PERM-{len(submitted)}"),
                broker.clock.timestamp_ns(),
            )

        # 只替换券商传输口; Gateway.submit_order -> RiskEngine -> ExecutionEngine 均为原生。
        monkeypatch.setattr(broker.execution, "_submit_order", submit_transport)
        if mismatch:
            native_submit = gateway.submit_order

            def omit_binding(order: Order, *, position_id: PositionId | None = None) -> None:
                del position_id
                native_submit(order)

            # 故障注入仍走原生执行, 重现旧版遗漏绑定后的 reduce_only 冲减失败。
            monkeypatch.setattr(gateway, "submit_order", omit_binding)

        async def fill(order: Order, quantity: int, trade: str) -> None:
            instrument = broker.cache.instrument(order.instrument_id)
            # 模拟券商净仓位回报的变化, 不直接修改 NT Cache。
            contract = broker.client._instrument_provider.contract[order.instrument_id]
            current = next(
                (
                    position.quantity
                    for position in broker.positions
                    if position.contract.conId == contract.conId
                ),
                Decimal(0),
            )
            broker.positions = [
                position
                for position in broker.positions
                if position.contract.conId != contract.conId
            ]
            broker.positions.append(
                IBPosition(
                    ACCOUNT.get_id(),
                    contract,
                    current + (quantity if order.side == OrderSide.BUY else -quantity),
                    100.0,
                )
            )
            broker.execution.generate_order_filled(
                order.strategy_id,
                order.instrument_id,
                order.client_order_id,
                order.venue_order_id,
                None,
                TradeId(trade),
                order.side,
                order.order_type,
                instrument.make_qty(quantity),
                instrument.make_price(100),
                Currency.from_str("USD"),
                Money(1, Currency.from_str("USD")),
                LiquiditySide.NO_LIQUIDITY_SIDE,
                broker.clock.timestamp_ns(),
            )
            await asyncio.sleep(0.03)

        monitor = asyncio.create_task(broker.session.monitor())
        try:
            await node.kernel.start_async()
            assert node.trader.is_running
            assert broker.session.status() == (True, False)
            repository = gateway._require_repository()
            now = broker.clock.timestamp_ns()
            event = TradeSignalEvent(
                strategy_name="patchtst_e3",
                target_weights=(
                    ("GOOG.US" if rotate else "AAPL.US", 0.0 if scenario == "reduce" else 0.25),
                ),
                rebalance_key="startup-recovery",
                reason="startup recovery test",
                expires_at_ns=now + 3_600_000_000_000,
                ts_event=now,
                ts_init=now,
                factor_context=FactorContext(
                    price_date.isoformat(),
                    "d" * 64,
                    "b" * 64,
                    "signal_inference",
                    CALENDAR_VERSION,
                    tuple(f"{symbol}.US" for symbol in symbols),
                    len(symbols),
                    str(tmp_path),
                ),
            )
            repository.register_signal_workflow(event, scope="default")
            broker.bus.publish(TRADE_SIGNAL_TOPIC, event)
            await asyncio.sleep(0.4)
            assert broker.positions_calls >= 2
            assert broker.session.status() == (True, False)
            gateway._poll_approved_signal(None)
            assert submitted == []
            assert repository.get_signal_workflow(str(event.id)).status == "NEW"
            snapshot._capture_snapshot(None)
            before = repository.latest_portfolio_snapshot(account_id=str(ACCOUNT))
            assert before is not None
            assert before.reconciliation_complete is False

            broker.complete_positions = True
            await broker.wait_ready()
            assert node.trader.is_running
            snapshot._capture_snapshot(None)
            after = repository.latest_portfolio_snapshot(account_id=str(ACCOUNT))
            assert after is not None
            assert after.reconciliation_complete is True
            assert after.not_ready_reason is None
            assert after.positions[0].signed_quantity == 9
            assert gateway._current_quantity(broker.instrument.id) == 9
            # NT 为恢复仓位推断的成交不能变成真实成交账本。
            assert repository.list_fill_audits(scope="default") == ()
            restored = broker.cache.positions_open(account_id=ACCOUNT)
            assert all(str(position.id).endswith("-EXTERNAL") for position in restored)
            if scenario != "unowned":
                for position in restored:
                    repository.record_order_event(
                        signal_event_id=str(event.id),
                        order_event_id=None,
                        timestamp_ns=now,
                        strategy_name=event.strategy_name,
                        instrument_id=str(position.instrument_id),
                        client_order_id=f"prior-{position.id}",
                        status="FILLED",
                        direction="BUY",
                        quantity=position.signed_qty,
                        reason="persisted original broker fill",
                    )
                    repository.record_fill(
                        run_id=None,
                        signal_event_id=str(event.id),
                        trade_id=f"prior-{position.id}",
                        timestamp_ns=now,
                        strategy_name=event.strategy_name,
                        instrument_id=str(position.instrument_id),
                        client_order_id=f"prior-{position.id}",
                        direction="BUY",
                        quantity=position.signed_qty,
                        price=100,
                        commission=1,
                    )
            assert gateway._account_rejection() is None
            gateway._poll_approved_signal(None)
            await asyncio.sleep(0.05)
            if scenario == "unowned":
                assert submitted == []
                return
            if scenario == "netting_rejected":
                assert submitted == []
                rows = repository.list_latest_order_audits(scope="default")
                assert rows[0].status == "DENIED"
                assert "not valid for NETTING OMS" in rows[0].reason
                return
            assert len(submitted) == (2 if rotate else 1)
            assert submitted[0].quantity.as_decimal() == (16 if scenario == "increase" else 9)
            gateway._poll_approved_signal(None)
            broker.bus.publish(TRADE_SIGNAL_TOPIC, event)
            assert len(submitted) == (2 if rotate else 1)
            assert repository.get_signal_workflow(str(event.id)).status == "ORDERS_SUBMITTED"
            if mismatch:
                # 让新一轮核对停在不完整回报, 证明账户摘要刷新也不能绕过门禁。
                broker.complete_positions = False
                await fill(submitted[0], 9, "mismatched-fill")
                broker.account_summary()
                assert broker.session.status() == (True, False)
                assert broker.cache.position(restored[0].id).signed_qty == 9
                assert str(event.id) not in gateway._pending_buys
                assert all(order.side == OrderSide.SELL for order in submitted)
                # 不调用采样接口: 失效入口必须自行落盘, 不能等待下个周期。
                assert not repository.latest_portfolio_snapshot(
                    account_id=str(ACCOUNT)
                ).reconciliation_complete
                if scenario == "mismatch":
                    invalid = repository.latest_portfolio_snapshot(account_id=str(ACCOUNT))
                    assert invalid.positions == after.positions
                    assert invalid.account_updated_at_utc == after.account_updated_at_utc
                    broker.complete_positions = True
                    current_task = broker.session._task
                    if current_task is not None:
                        await asyncio.shield(current_task)
                    # 净数量相抵不能把同证券多条仓位重新认定为就绪。
                    await asyncio.sleep(0.1)
                    assert broker.session.status() == (True, False)
            else:
                first = submitted[0]
                await fill(first, 4, "partial-first")
                assert broker.cache.position(restored[0].id).signed_qty == (
                    13 if scenario == "increase" else 5
                )
                assert broker.session.status() == (True, True)
                if rotate:
                    # 后提交的卖单先终结, 第一笔仍部分成交时不得买入。
                    await fill(submitted[1], 5, "second-sell")
                    assert len(submitted) == 2
                await fill(first, int(first.quantity.as_double()) - 4, "remaining-first")
                assert broker.session.status() == (True, True)
                if rotate:
                    assert len(submitted) == 3
                    assert submitted[2].side == OrderSide.BUY
                    assert str(submitted[2].instrument_id) == "GOOG.NASDAQ"
                    assert submitted[2].quantity.as_double() == 25
                    await fill(submitted[2], 25, "new-position")
                positions = broker.cache.positions_open(account_id=ACCOUNT)
                if scenario == "reduce":
                    assert positions == []
                elif scenario == "increase":
                    assert len(positions) == 1
                    assert positions[0].id == restored[0].id
                    assert positions[0].signed_qty == 25
                else:
                    assert len(positions) == 1
                    assert str(positions[0].instrument_id) == "GOOG.NASDAQ"
                    assert positions[0].signed_qty == 25
                assert gateway._account_rejection() is None
                # 换仓后完整核对仍只能得到实际唯一持仓, 不生成并存的虚拟仓位。
                broker.session.invalidate("Verify reconciliation after fills")
                assert not broker.session.status()[1]
                await broker.wait_ready()
                refreshed = broker.cache.positions_open(account_id=ACCOUNT)
                assert [(position.id, position.signed_qty) for position in refreshed] == [
                    (position.id, position.signed_qty) for position in positions
                ]
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await broker.session.close()
            await node.kernel.stop_async()
            await asyncio.gather(
                node.kernel.data_engine.get_cmd_queue_task(),
                node.kernel.data_engine.get_req_queue_task(),
                node.kernel.data_engine.get_res_queue_task(),
                node.kernel.data_engine.get_data_queue_task(),
                node.kernel.risk_engine.get_cmd_queue_task(),
                node.kernel.risk_engine.get_evt_queue_task(),
                broker.engine.get_cmd_queue_task(),
                broker.engine.get_evt_queue_task(),
                return_exceptions=True,
            )
            node.kernel.dispose()
            for callback in broker.callbacks:
                callback.cancel()

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", ["quantity", "unknown_contract"])
def test_raw_positions_must_match_native_report_and_cache(mismatch: str) -> None:
    async def run() -> None:
        async with _broker(quantity=9) as broker:
            original = list(broker.positions)
            reported = asyncio.Event()

            def change_after_report(report: object) -> None:
                if not isinstance(report, ExecutionMassStatus):
                    return
                # 报告已恢复 Cache 后, 下一份原始回报出现数量变化或未知合约。
                broker.positions = [
                    IBPosition(
                        ACCOUNT.get_id(),
                        broker.contract
                        if mismatch == "quantity"
                        else IBContract(conId=999, symbol="UNKNOWN", secType="STK", currency="USD"),
                        Decimal(16 if mismatch == "quantity" else 9),
                        100.0,
                    )
                ]
                reported.set()

            broker.bus.subscribe("reports.execution.*", change_after_report)
            monitor = asyncio.create_task(broker.session.monitor())
            try:
                await asyncio.wait_for(reported.wait(), 2)
                task = broker.session._task
                assert task is not None
                assert await asyncio.shield(task) is False
                assert broker.session.status() == (True, False)
                broker.bus.unsubscribe("reports.execution.*", change_after_report)
                broker.positions = original
                await broker.wait_ready()
                assert (
                    sum(
                        item.signed_decimal_qty()
                        for item in broker.cache.positions_open(account_id=ACCOUNT)
                    )
                    == 9
                )
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)

    asyncio.run(run())
