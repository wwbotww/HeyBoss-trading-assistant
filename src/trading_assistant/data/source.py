"""历史日线数据源的供应商无关接口。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument

from trading_assistant.data.config import InstrumentSpec


class HistoricalBarSource(Protocol):
    """数据管道使用的最小历史日线接口。"""

    async def connect(self) -> None:
        """连接或初始化数据源。"""

    async def request_instruments(
        self,
        specs: Sequence[InstrumentSpec],
    ) -> list[Instrument]:
        """把配置标的解析为 NT 原生 Instrument。"""

    async def request_daily_bars(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """请求一个标的的完整日线。"""

    async def close(self) -> None:
        """释放数据源连接。"""
