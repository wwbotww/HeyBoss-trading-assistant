"""原生 LiveDataEngine 的异步读取、故障恢复与迟到响应回归。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.core.data import Data
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.messages import DataResponse, RequestBars, RequestData
from nautilus_trader.live.data_engine import LiveDataEngine
from nautilus_trader.model.data import BarType, CustomData
from nautilus_trader.model.identifiers import ClientId, TraderId

from tests.data.helpers import make_bar
from tests.integration.test_paper_daily import _publisher
from trading_assistant.data.catalog import CatalogRepository, CatalogRequestOutcome
from trading_assistant.data.factor import FACTOR_DATA_TYPE
from trading_assistant.live.catalog_client import CatalogDataClient, CatalogDataClientConfig

BAR_TYPE = BarType.from_str("AAPL.US-1-DAY-LAST-EXTERNAL")


class _Harness:
    def __init__(self, path: Path, timeout: float) -> None:
        self.loop = asyncio.get_running_loop()
        self.clock = LiveClock()
        self.cache = Cache()
        self.bus = MessageBus(trader_id=TraderId("TESTER-ASYNC"), clock=self.clock)
        self.engine = LiveDataEngine(
            loop=self.loop, msgbus=self.bus, cache=self.cache, clock=self.clock
        )
        self.client = CatalogDataClient(
            self.loop,
            "CATALOG",
            CatalogDataClientConfig(catalog_path=str(path), request_timeout_seconds=timeout),
            self.bus,
            self.cache,
            self.clock,
        )
        self.engine.register_client(self.client)

    def request(
        self, *, factor: bool = False, outcome: CatalogRequestOutcome | None = None
    ) -> tuple[RequestData, asyncio.Future[DataResponse], CatalogRequestOutcome]:
        result: asyncio.Future[DataResponse] = self.loop.create_future()
        outcome = outcome or CatalogRequestOutcome()
        arguments = {
            "start": datetime(2025, 1, 1, tzinfo=UTC),
            "end": datetime(2025, 1, 7, tzinfo=UTC),
            "limit": 0,
            "client_id": ClientId("CATALOG"),
            "venue": None,
            "callback": result.set_result,
            "request_id": UUID4(),
            "ts_init": self.clock.timestamp_ns(),
            "params": {"catalog_outcome": outcome},
        }
        request = (
            RequestData(data_type=FACTOR_DATA_TYPE, instrument_id=None, **arguments)
            if factor
            else RequestBars(bar_type=BAR_TYPE, **arguments)
        )
        self.bus.request(endpoint="DataEngine.request", request=request)
        return request, result, outcome


@asynccontextmanager
async def _native_engine(path: Path, request_deadline: float = 2) -> AsyncIterator[_Harness]:
    CatalogRepository(path)
    harness = _Harness(path, request_deadline)
    harness.engine.start()
    try:
        yield harness
    finally:
        await harness.client._disconnect()
        await asyncio.sleep(0)
        harness.engine.stop()
        tasks = [
            harness.engine.get_cmd_queue_task(),
            harness.engine.get_req_queue_task(),
            harness.engine.get_res_queue_task(),
            harness.engine.get_data_queue_task(),
        ]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert all(task.done() for task in harness.client._tasks)
        assert harness.client._worker is None or harness.client._worker.done()


@pytest.mark.parametrize("factor", [False, True])
def test_busy_interrupted_and_recovered_requests_complete_native_lifecycle(
    tmp_path: Path, factor: bool
) -> None:
    async def run() -> None:
        catalog = CatalogRepository(tmp_path)
        catalog.replace_bars([make_bar(date(2025, 1, 2), instrument_id="AAPL.US")])
        async with _native_engine(tmp_path) as native:
            with _publisher(tmp_path):
                request, response, outcome = native.request(factor=factor)
                received = await asyncio.wait_for(response, 2)
                assert outcome.status == "busy"
                assert received.params["catalog_outcome"] is outcome
                assert not native.bus.is_pending_request(request.id)
                assert not native.cache.bars(BAR_TYPE)
            request, response, outcome = native.request(factor=factor)
            await asyncio.wait_for(response, 2)
            assert outcome.status == "ok"
            assert outcome.rows_received == (0 if factor else 1)
            assert not native.bus.is_pending_request(request.id)
            with _publisher(tmp_path) as publisher:
                publisher.kill()
                publisher.wait(timeout=5)
            request, response, outcome = native.request(factor=factor)
            await asyncio.wait_for(response, 2)
            assert outcome.status == "failed"
            assert outcome.reason == "RuntimeError"
            assert not native.bus.is_pending_request(request.id)
            assert native.engine.is_running

    asyncio.run(run())


def test_timeout_keeps_one_physical_reader_and_discards_late_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        entered, release = Event(), Event()
        calls = 0

        def slow_read(_: RequestData) -> list[Data | CustomData]:
            nonlocal calls
            calls += 1
            entered.set()
            assert release.wait(timeout=3)
            return [make_bar(date(2025, 1, 2), instrument_id="AAPL.US")]

        async with _native_engine(tmp_path, request_deadline=0.1) as native:
            monkeypatch.setattr(native.client, "_read", slow_read)
            request, response, outcome = native.request()
            heartbeats = 0
            try:
                while not response.done():
                    await asyncio.sleep(0.005)
                    heartbeats += 1
                assert entered.is_set()
                assert heartbeats >= 5
                assert outcome.reason == "catalog_request_timeout"
                assert not native.bus.is_pending_request(request.id)
                _, second, second_outcome = native.request()
                await asyncio.wait_for(second, 2)
                assert second_outcome.reason == "catalog_request_timeout"
                assert calls == 1
            finally:
                release.set()
            assert native.client._worker is not None
            await native.client._worker
            assert native.cache.bars(BAR_TYPE) == []
            _, recovered, recovered_outcome = native.request()
            await asyncio.wait_for(recovered, 2)
            assert recovered_outcome.status == "ok"
            assert native.cache.bar(BAR_TYPE).close.as_double() == 101
            assert calls == 2

    asyncio.run(run())


def test_cancelled_generation_cannot_publish_and_cleans_correlation(tmp_path: Path) -> None:
    async def run() -> None:
        CatalogRepository(tmp_path).replace_bars(
            [make_bar(date(2025, 1, 2), instrument_id="AAPL.US")]
        )
        async with _native_engine(tmp_path) as native:
            request, response, outcome = native.request(
                outcome=CatalogRequestOutcome(status="cancelled")
            )
            await asyncio.wait_for(response, 2)
            assert outcome.status == "cancelled"
            assert outcome.rows_received == 0
            assert not native.bus.is_pending_request(request.id)
            assert native.cache.bars(BAR_TYPE) == []

    asyncio.run(run())


def test_disconnect_completes_requests_not_yet_dispatched(tmp_path: Path) -> None:
    async def run() -> None:
        async with _native_engine(tmp_path) as native:
            request, response, outcome = native.request()
            await native.client._disconnect()
            await asyncio.wait_for(response, 2)
            assert outcome.status == "cancelled"
            assert not native.bus.is_pending_request(request.id)
            assert not native.client._pending
            assert native.cache.bars(BAR_TYPE) == []

    asyncio.run(run())
