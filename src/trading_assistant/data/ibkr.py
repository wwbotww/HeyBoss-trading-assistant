"""IBKR 历史数据源的 NautilusTrader 适配层。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime

from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.historical.client import (
    HistoricInteractiveBrokersClient,
)
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument

from trading_assistant.data.config import InstrumentSpec


class IbkrHistoricalBarSource:
    """基于 NT HistoricInteractiveBrokersClient 的 IBKR 数据源。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        client_id: int,
        use_regular_trading_hours: bool,
        request_timeout_seconds: int,
        log_level: str,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._use_regular_trading_hours = use_regular_trading_hours
        self._request_timeout_seconds = request_timeout_seconds
        self._log_level = log_level
        self._client: HistoricInteractiveBrokersClient | None = None

    async def connect(self) -> None:
        """创建并连接 NT 历史客户端。"""
        if self._client is not None:
            return
        self._client = HistoricInteractiveBrokersClient(
            host=self._host,
            port=self._port,
            client_id=self._client_id,
            log_level=self._log_level,
            instrument_provider_config=InteractiveBrokersInstrumentProviderConfig(),
        )
        await self._client.connect()

    def _connected_client(self) -> HistoricInteractiveBrokersClient:
        """返回已连接客户端; 否则拒绝请求。"""
        if self._client is None:
            raise RuntimeError("Historical data source is not connected")
        return self._client

    async def request_instruments(
        self,
        specs: Sequence[InstrumentSpec],
    ) -> list[Instrument]:
        """使用 NT IB_SIMPLIFIED 规则解析标的。"""
        client = self._connected_client()
        instrument_ids = [InstrumentId.from_str(spec.instrument_id) for spec in specs]
        return await client.request_instruments(instrument_ids=list(instrument_ids))

    async def request_daily_bars(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """请求标准 1-DAY-LAST-EXTERNAL RTH Bar。"""
        client = self._connected_client()
        instrument_id = InstrumentId.from_str(spec.instrument_id)
        return await client.request_bars(
            bar_specifications=["1-DAY-LAST"],
            start_date_time=start,
            end_date_time=end,
            tz_name="UTC",
            instrument_ids=[instrument_id],
            use_rth=self._use_regular_trading_hours,
            timeout=self._request_timeout_seconds,
        )

    async def close(self) -> None:
        """集中处理 NT 1.230.0 历史客户端缺少公开 close 的兼容逻辑。"""
        client = self._client
        if client is None:
            return
        raw_client = client._client
        if not raw_client.is_stopped:
            raw_client.stop()
        await asyncio.sleep(0.1)
        raw_client.dispose()
        self._client = None
