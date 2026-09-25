"""隔离验证 NT 历史请求、共享卷和网页读取, 不连接交易账户。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.model.data import BarType, CustomData
from nautilus_trader.model.identifiers import ClientId, TraderId
from nautilus_trader.portfolio.portfolio import Portfolio

from tests.data.helpers import make_bar
from trading_assistant.application.portfolio import PortfolioQueryService
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.data.market_calendar import CALENDAR_VERSION
from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, TradeSignalEvent
from trading_assistant.execution.gateway import ExecutionGatewayConfig, ExecutionGatewayStrategy
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.patchtst_factor import (
    PatchTSTFactorActor,
    PatchTSTFactorActorConfig,
)


@contextmanager
def _publisher(path: Path) -> Iterator[subprocess.Popen[str]]:
    code = """
import sys
from pathlib import Path
from trading_assistant.data.catalog import catalog_lock
with catalog_lock(Path(sys.argv[1]), exclusive=True):
    print('locked', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(  # noqa: S603 -- 固定 Python 子进程只持有临时目录锁。
        [sys.executable, "-c", code, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "locked"
    try:
        yield process
    finally:
        process.communicate(timeout=5)
        assert process.returncode in (0, -9)


def test_nt_and_web_cannot_consume_catalog_during_publication_or_after_writer_kill(
    tmp_path: Path,
) -> None:
    catalog = CatalogRepository(tmp_path / "catalog")
    bar = make_bar(date(2025, 1, 2), instrument_id="AAPL.US")
    catalog.replace_bars([bar])
    clock = TestClock()
    clock.set_time(int(datetime(2025, 1, 3, tzinfo=UTC).timestamp() * 1e9))
    cache = Cache()
    msgbus = MessageBus(trader_id=TraderId("TESTER-001"), clock=clock)
    engine = DataEngine(msgbus=msgbus, cache=cache, clock=clock)
    engine.register_catalog(catalog.catalog)
    responses: list[object] = []

    def request() -> None:
        msgbus.request(
            endpoint="DataEngine.request",
            request=RequestBars(
                bar_type=bar.bar_type,
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 1, 3, tzinfo=UTC),
                limit=0,
                client_id=ClientId("CATALOG"),
                venue=None,
                callback=responses.append,
                request_id=UUID4(),
                ts_init=clock.timestamp_ns(),
                params={"update_catalog": False, "join_request": False},
            ),
        )

    service = PortfolioQueryService(
        repository=None,
        account_id=None,
        catalog_path=tmp_path / "catalog",
        instruments=(),
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        stale_after_seconds=90,
    )
    with _publisher(tmp_path / "catalog"):
        with pytest.raises(TimeoutError, match="Catalog is busy"):
            request()
        assert service._latest_reference_price(catalog, "AAPL.US") == (None, None)
        assert not responses
    request()
    assert len(responses) == 1
    assert service._latest_reference_price(catalog, "AAPL.US")[0] == 101
    with _publisher(tmp_path / "catalog") as process:
        process.kill()
        process.wait(timeout=5)
    with pytest.raises(RuntimeError, match="interrupted"):
        request()
    assert service._latest_reference_price(catalog, "AAPL.US") == (None, None)
    with pytest.raises(RuntimeError, match="interrupted"):
        catalog.replace_bars([bar])
    assert (tmp_path / "catalog" / ".catalog-writing").exists()


def test_native_data_engine_reads_new_daily_prices_after_publication(tmp_path: Path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog")
    clock = TestClock()
    clock.set_time(int(datetime(2025, 1, 7, tzinfo=UTC).timestamp() * 1e9))
    cache = Cache()
    msgbus = MessageBus(trader_id=TraderId("TESTER-002"), clock=clock)
    engine = DataEngine(msgbus=msgbus, cache=cache, clock=clock)
    engine.register_catalog(catalog.catalog)
    bar_type = BarType.from_str("AAPL.US-1-DAY-LAST-EXTERNAL")
    for day, close in ((date(2025, 1, 2), 101), (date(2025, 1, 3), 102)):
        catalog.append_new_bars([make_bar(day, instrument_id="AAPL.US", close=close)])
        msgbus.request(
            endpoint="DataEngine.request",
            request=RequestBars(
                bar_type=bar_type,
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 1, 6, tzinfo=UTC),
                limit=0,
                client_id=ClientId("CATALOG"),
                venue=None,
                callback=lambda _: None,
                request_id=UUID4(),
                ts_init=clock.timestamp_ns(),
                params={"update_catalog": False, "join_request": False},
            ),
        )
        assert cache.bar(bar_type).close.as_double() == close


@pytest.mark.parametrize("clock_offset_ns", [0, 928])
def test_gateway_refreshes_after_close_startup_and_later_factor_publication(
    tmp_path: Path, clock_offset_ns: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nautilus_trader.accounting.accounts.margin import MarginAccount
    from nautilus_trader.model.identifiers import AccountId
    from nautilus_trader.test_kit.stubs.events import TestEventStubs

    from trading_assistant.execution.events import FactorContext
    from trading_assistant.execution.gateway import _PlannedOrder

    catalog = CatalogRepository(tmp_path / "catalog")
    clock = TestClock()
    first = int(datetime(2025, 1, 3, 22, tzinfo=UTC).timestamp() * 1e9) + clock_offset_ns
    clock.set_time(first)
    from tests.live.test_catalog_client import _native_engine

    async def run() -> None:
        async with _native_engine(tmp_path / "catalog") as native:
            cache = native.cache
            cache.add_account(
                MarginAccount(
                    TestEventStubs.margin_account_state(AccountId("IB-DU123")),
                    calculate_account_state=False,
                )
            )
            trader_id = TraderId("TESTER-ASYNC")
            msgbus = native.bus
            portfolio = Portfolio(msgbus=msgbus, cache=cache, clock=clock)
            gateway = ExecutionGatewayStrategy(
                ExecutionGatewayConfig(
                    instrument_routes={"AAPL.US": "AAPL.NASDAQ"},
                    execution_bar_types={"AAPL.US": "AAPL.US-1-DAY-LAST-EXTERNAL"},
                    approval_mode="auto",
                    database_url=f"sqlite:///{tmp_path / 'live.db'}",
                    account_id="IB-DU123",
                    strategy_capital_usd=10000,
                    max_order_notional_usd=10000,
                    max_instrument_weight=0.25,
                    max_daily_new_positions=3,
                    max_gross_exposure=0.8,
                    bootstrap_from_catalog=True,
                    model_release_id="b" * 64,
                )
            )
            gateway.register(trader_id, portfolio, msgbus, cache, clock)
            catalog.replace_bars([make_bar(date(2025, 1, 2), instrument_id="AAPL.US")])
            with _publisher(tmp_path / "catalog"):
                gateway.on_start()
                async with asyncio.timeout(2):
                    while gateway._execution_bar_requests:  # noqa: ASYNC110 -- NT 未暴露完成事件, 有界观察其原生状态。
                        await asyncio.sleep(0.01)
                assert not gateway._execution_prices_ready
            clock.set_time(first + 61_000_000_000)
            gateway._poll_approved_signal(None)
            async with asyncio.timeout(2):
                while gateway._execution_bar_requests:  # noqa: ASYNC110 -- NT 未暴露完成事件, 有界观察其原生状态。
                    await asyncio.sleep(0.01)
            assert gateway._execution_prices_ready
            assert gateway._execution_price_date is None
            catalog.append_new_bars(
                [make_bar(date(2025, 1, 3), instrument_id="AAPL.US", close=102)]
            )
            clock.set_time(
                int(datetime(2025, 1, 6, 13, tzinfo=UTC).timestamp() * 1e9) + clock_offset_ns
            )
            context = FactorContext(
                "2025-01-03",
                "d" * 64,
                "b" * 64,
                "signal_inference",
                CALENDAR_VERSION,
                ("AAPL.US",),
                1,
                str(tmp_path / "catalog"),
            )
            repository = gateway._require_repository()
            repository.record_factor_import(
                catalog_path=context.catalog_path,
                delivery_id=context.delivery_id,
                mode="paper",
                model_release_id=context.model_release_id,
                calendar_version=CALENDAR_VERSION,
                source_created_at=datetime(2025, 1, 6, 12, tzinfo=UTC),
                verified_at=datetime(2025, 1, 6, 13, tzinfo=UTC),
            )
            opening_ns = int(datetime(2025, 1, 6, 14, 30, tzinfo=UTC).timestamp() * 1e9)
            event = TradeSignalEvent(
                strategy_name="patchtst_e3",
                target_weights=(("AAPL.US", 0.25),),
                rebalance_key="after-close",
                reason="later publication",
                factor_context=context,
                not_before_ns=opening_ns,
                expires_at_ns=int(datetime(2025, 1, 6, 21, tzinfo=UTC).timestamp() * 1e9),
                ts_event=clock.timestamp_ns(),
                ts_init=clock.timestamp_ns(),
            )
            repository.register_signal_workflow(event, scope="default")
            executed: list[tuple[_PlannedOrder, ...]] = []

            def execute(
                self: ExecutionGatewayStrategy,
                signal: TradeSignalEvent,
                plan: tuple[_PlannedOrder, ...],
            ) -> None:
                del self
                assert signal.id == event.id
                executed.append(plan)

            monkeypatch.setattr(ExecutionGatewayStrategy, "_execute_plan", execute)
            # 新信号自身必须触发刷新; 发布持锁时保持 NEW, 不直接调用刷新函数。
            with _publisher(tmp_path / "catalog"):
                gateway._handle_signal(event)
                async with asyncio.timeout(2):
                    while gateway._execution_bar_requests:  # noqa: ASYNC110 -- 原生数据请求无完成事件。
                        await asyncio.sleep(0.01)
                assert not gateway._execution_prices_ready
                workflow = repository.get_signal_workflow(str(event.id))
                assert workflow is not None
                assert workflow.status == "NEW"
                assert not executed
            gateway._poll_approved_signal(None)
            async with asyncio.timeout(2):
                while gateway._execution_bar_requests:  # noqa: ASYNC110 -- NT 未暴露完成事件, 有界观察其原生状态。
                    await asyncio.sleep(0.01)
            assert gateway._execution_price_date == date(2025, 1, 3)
            assert (
                cache.bar(BarType.from_str("AAPL.US-1-DAY-LAST-EXTERNAL")).close.as_double() == 102
            )
            workflow = repository.get_signal_workflow(str(event.id))
            assert workflow is not None
            assert workflow.status == "NEW"
            assert not executed
            clock.set_time(opening_ns)
            gateway._poll_approved_signal(None)
            gateway._poll_approved_signal(None)
            assert len(executed) == 1
            assert executed[0][0].price == 102
            assert executed[0][0].quantity == 24
            workflow = repository.get_signal_workflow(str(event.id))
            assert workflow is not None
            assert workflow.status == "ORDERS_SUBMITTED"
            gateway.on_stop()

    asyncio.run(run())


def test_native_factor_history_keeps_nanosecond_clock_boundary(tmp_path: Path) -> None:
    catalog = CatalogRepository(tmp_path / "catalog")
    timestamp_ns = int(datetime(2025, 1, 3, tzinfo=UTC).timestamp()) * 1_000_000_000
    row = FactorScoreData(
        calendar_version=CALENDAR_VERSION,
        canonical_id="AAPL.US",
        security_id="isin:US0378331005",
        asof_date="2025-01-02",
        score=1.0,
        eligible=True,
        batch_id="delivery:2025-01-02",
        batch_size=1,
        delivery_id="d" * 64,
        model_release_id="b" * 64,
        source_kind="signal_inference",
        ts_event=timestamp_ns,
        ts_init=timestamp_ns,
    )
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, row)])
    catalog.replace_bars([make_bar(date(2025, 1, 2), instrument_id="AAPL.US")])
    database_url = f"sqlite:///{tmp_path / 'live.db'}"
    repository = TradingRepository(database_url)
    repository.create_schema()
    repository.record_factor_import(
        catalog_path=str(tmp_path / "catalog"),
        delivery_id=row.delivery_id,
        mode="paper",
        model_release_id=row.model_release_id,
        calendar_version=CALENDAR_VERSION,
        source_created_at=datetime(2025, 1, 3, tzinfo=UTC),
        verified_at=datetime(2025, 1, 3, 12, tzinfo=UTC),
    )
    repository.close()
    clock = TestClock()
    # 928ns 经浮点秒转 datetime 会向未来舍入; 原生历史请求必须保留原边界。
    clock.set_time(timestamp_ns + 13 * 3600 * 1_000_000_000 + 928)
    from tests.live.test_catalog_client import _native_engine

    async def run() -> None:
        async with _native_engine(tmp_path / "catalog") as native:
            cache = native.cache
            msgbus = native.bus
            portfolio = Portfolio(msgbus=msgbus, cache=cache, clock=clock)
            actor = PatchTSTFactorActor(
                PatchTSTFactorActorConfig(
                    instrument_ids=("AAPL.US",),
                    top_n=1,
                    target_gross_exposure=0.25,
                    signal_expiry_hours=24,
                    database_url=database_url,
                    stream_data=False,
                    bootstrap_from_catalog=True,
                    model_release_id=row.model_release_id,
                    catalog_path=str(tmp_path / "catalog"),
                )
            )
            actor.register_base(portfolio, msgbus, cache, clock)
            gateway = ExecutionGatewayStrategy(
                ExecutionGatewayConfig(
                    instrument_routes={"AAPL.US": "AAPL.NASDAQ"},
                    execution_bar_types={"AAPL.US": "AAPL.US-1-DAY-LAST-EXTERNAL"},
                    approval_mode="auto",
                    database_url=database_url,
                    account_id="IB-DU123",
                    strategy_capital_usd=10000,
                    max_order_notional_usd=10000,
                    max_instrument_weight=0.25,
                    max_daily_new_positions=3,
                    max_gross_exposure=0.8,
                    bootstrap_from_catalog=True,
                    broker_account_stale_after_seconds=300,
                )
            )
            gateway.register(TraderId("TESTER-ASYNC"), portfolio, msgbus, cache, clock)
            gateway.bind_broker_status(lambda: (True, False), invalidate=lambda _: None)
            events: list[TradeSignalEvent] = []
            msgbus.subscribe(TRADE_SIGNAL_TOPIC, events.append)
            try:
                with _publisher(tmp_path / "catalog"):
                    gateway.on_start()
                    actor.start()
                    async with asyncio.timeout(2):
                        while actor.has_pending_requests() or gateway._execution_bar_requests:  # noqa: ASYNC110 -- 同时观察两个 NT 原生请求。
                            await asyncio.sleep(0.01)
                    assert events == []
                    assert not gateway._execution_prices_ready
                    assert native.engine.is_running
                actor._request_catalog_history()
                gateway._poll_approved_signal(None)
                async with asyncio.timeout(2):
                    while actor.has_pending_requests() or gateway._execution_bar_requests:  # noqa: ASYNC110 -- 同时观察两个 NT 原生请求。
                        await asyncio.sleep(0.01)
                assert len(events) == 1
                assert events[0].target_weights == (("AAPL.US", 0.25),)
                assert events[0].factor_context is not None
                assert events[0].factor_context.delivery_id == row.delivery_id
                assert not actor.has_pending_requests()
                assert gateway._execution_prices_ready
                assert gateway._execution_price_date == date(2025, 1, 2)
                workflow = gateway._require_repository().get_signal_workflow(str(events[0].id))
                assert workflow is not None
                assert workflow.status == "NEW"
                assert cache.orders() == []
                actor._catalog_request_completed(
                    UUID4(),
                    generation=actor._request_generation,
                    outcome=actor._request_outcome,
                )
                assert len(events) == 1
                # 成功的空读取不能重新发布已缓存横截面。
                catalog.catalog.delete_data_range(FactorScoreData, identifier="")
                actor._request_catalog_history()
                async with asyncio.timeout(2):
                    while actor.has_pending_requests():  # noqa: ASYNC110 -- NT 原生状态。
                        await asyncio.sleep(0.01)
                assert len(events) == 1
                assert actor._request_outcome is not None
                assert actor._request_outcome.status == "ok"
                assert actor._request_outcome.rows_received == 0
                # 重复获取完整横截面保持原始工作流身份。
                catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, row)])
                actor._request_catalog_history()
                async with asyncio.timeout(2):
                    while actor.has_pending_requests():  # noqa: ASYNC110 -- NT 原生状态。
                        await asyncio.sleep(0.01)
                assert len(events) == 2
                assert events[0].id == events[1].id
            finally:
                actor.stop()
                gateway.on_stop()

    asyncio.run(run())
