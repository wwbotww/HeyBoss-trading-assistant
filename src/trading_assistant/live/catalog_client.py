"""将本地 Catalog 的阻塞读取隔离在单个工作线程, 由 NT 交付历史数据。"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.data import Data
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.messages import RequestBars, RequestData
from nautilus_trader.live.config import LiveDataClientConfig
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.identifiers import ClientId

from trading_assistant.data.catalog import (
    CatalogBusyError,
    CatalogRepository,
    CatalogRequestOutcome,
)
from trading_assistant.data.factor import FactorScoreData


class CatalogDataClientConfig(LiveDataClientConfig, frozen=True, kw_only=True):
    """NT 客户端工厂所需的本地路径和单次请求期限。"""

    catalog_path: str
    request_timeout_seconds: float = 30


class CatalogDataClient(LiveMarketDataClient):
    """仅处理因子和 Bar 历史请求; 不建立行情或交易连接。"""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: CatalogDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> None:
        super().__init__(
            loop, ClientId(name), None, msgbus, cache, clock, InstrumentProvider(), config
        )
        if config.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self._settings = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catalog-read")
        self._read_lock = asyncio.Lock()
        self._worker: asyncio.Future[list[Data | CustomData]] | None = None
        self._closing = False
        self._pending: dict[UUID4, RequestData] = {}

    async def _connect(self) -> None:
        """目录由启动预检验证; 每轮读取仍独立检查锁与中断标记。"""

    async def _disconnect(self) -> None:
        """取消逻辑请求, 有界等待实际读取; 不把取消误认为线程已经停止。"""
        self._closing = True
        await self.cancel_pending_tasks()
        # 尚未得到首次调度的协程没有机会进入异常处理, 也必须清理其 NT 请求。
        for request in tuple(self._pending.values()):
            outcome = request.params.get("catalog_outcome")
            if isinstance(outcome, CatalogRequestOutcome):
                outcome.status, outcome.reason = "cancelled", "client_disconnected"
            self._respond(request, [])
        if self._worker is not None and not self._worker.done():
            await asyncio.wait({self._worker}, timeout=self._settings.request_timeout_seconds)
            if not self._worker.done():
                self._log.warning("Catalog reader is still finishing after shutdown deadline")
        self._executor.shutdown(wait=False, cancel_futures=True)

    def request(self, request: RequestData) -> None:
        """在调度前登记, 覆盖协程尚未开始便被停止的情况。"""
        if self._closing:
            outcome = request.params.get("catalog_outcome")
            if isinstance(outcome, CatalogRequestOutcome):
                outcome.status, outcome.reason = "cancelled", "client_disconnected"
            self._respond(request, [])
            return
        self._pending[request.id] = request
        self.create_task(self._request(request))

    def request_bars(self, request: RequestBars) -> None:
        """两类历史请求共用同一读取队列与生命周期。"""
        self.request(request)

    def _read(self, request: RequestData) -> list[Data | CustomData]:
        """工作线程只读 Catalog, 不接触 NT Cache、消息总线或请求结果。"""
        repository = CatalogRepository(Path(self._settings.catalog_path))
        if isinstance(request, RequestBars):
            rows: list[Data | CustomData] = list(
                repository.read_bars(
                    request.bar_type,
                    start_ns=None if request.start is None else request.start.value,
                    end_ns=None if request.end is None else request.end.value,
                )
            )
        elif request.data_type.type is FactorScoreData:
            rows = repository.catalog.query(FactorScoreData, start=request.start, end=request.end)
        else:
            raise ValueError(f"Unsupported Catalog request: {request.data_type}")
        rows.sort(key=lambda row: row.ts_init)
        return rows[-request.limit :] if request.limit else rows

    async def _request_bars(self, request: RequestBars) -> None:
        await self._request(request)

    async def _request(self, request: RequestData) -> None:
        outcome = request.params.get("catalog_outcome")
        if not isinstance(outcome, CatalogRequestOutcome):
            # 缺少结果契约仍须完成 NT 清理, 但绝不交付未受保护的数据。
            self._log.error("Catalog request is missing its outcome")
            self._respond(request, [])
            return
        rows: list[Data | CustomData] = []
        try:
            if self._closing:
                raise asyncio.CancelledError
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                async with self._read_lock:
                    # 超时不会停止线程; 后续请求等待它真正退出后才能启动读取。
                    if self._worker is not None and not self._worker.done():
                        await asyncio.wait({self._worker})
                    if outcome.status != "pending":
                        raise asyncio.CancelledError
                    self._worker = self._loop.run_in_executor(self._executor, self._read, request)
                    self._worker.add_done_callback(self._consume_worker_exception)
                    rows = await asyncio.shield(self._worker)
            if outcome.status == "pending":
                outcome.status = "ok"
                outcome.rows_received = len(rows)
        except CatalogBusyError:
            outcome.status, outcome.reason = "busy", "catalog_busy"
        except asyncio.CancelledError:
            outcome.status, outcome.reason = "cancelled", "request_cancelled"
        except TimeoutError:
            outcome.status, outcome.reason = "failed", "catalog_request_timeout"
        except Exception as exc:
            outcome.status, outcome.reason = "failed", type(exc).__name__
            self._log.error(f"Catalog read failed: {type(exc).__name__}: {exc}")
        if outcome.status != "ok":
            rows = []
            outcome.rows_received = 0
        self._respond(request, rows)

    @staticmethod
    def _consume_worker_exception(worker: asyncio.Future[list[Data | CustomData]]) -> None:
        """逻辑请求已超时时也消费线程异常, 防止遗留未读取的 Future。"""
        if not worker.cancelled():
            worker.exception()

    def _respond(self, request: RequestData, rows: list[Data | CustomData]) -> None:
        """成功、失败和取消均走 NT 原生响应与关联请求清理。"""
        self._pending.pop(request.id, None)
        if isinstance(request, RequestBars):
            self._handle_bars(
                request.bar_type, rows, request.id, request.start, request.end, request.params
            )
        else:
            self._handle_data_response(
                request.data_type, rows, request.id, request.start, request.end, request.params
            )


class CatalogDataClientFactory(LiveDataClientFactory):
    """NT TradingNode 装配要求的具体客户端工厂。"""

    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: LiveDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> CatalogDataClient:
        if not isinstance(config, CatalogDataClientConfig):
            raise TypeError("CATALOG requires CatalogDataClientConfig")
        return CatalogDataClient(loop, name, config, msgbus, cache, clock)
