"""IBKR 历史数据源适配层测试。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import ClassVar

import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from tests.data.helpers import make_bar
from trading_assistant.data import ibkr as ibkr_module
from trading_assistant.data.ibkr import IbkrHistoricalBarSource


class FakeRawClient:
    """记录 NT 底层客户端是否正确停止和释放。"""

    def __init__(self) -> None:
        self.is_stopped = False
        self.stop_count = 0
        self.dispose_count = 0

    def stop(self) -> None:
        self.is_stopped = True
        self.stop_count += 1

    def dispose(self) -> None:
        self.dispose_count += 1


class FakeHistoricClient:
    """替代真实 NT 历史客户端、验证参数与生命周期。"""

    instances: ClassVar[list[FakeHistoricClient]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.connect_count = 0
        self.instrument_ids: list[InstrumentId] = []
        self.bar_request: dict[str, object] = {}
        self._client = FakeRawClient()
        self.instances.append(self)

    async def connect(self) -> None:
        self.connect_count += 1

    async def request_instruments(
        self,
        instrument_ids: list[InstrumentId],
    ) -> list[Instrument]:
        self.instrument_ids = instrument_ids
        return [TestInstrumentProvider.equity("SPY", "ARCA")]

    async def request_bars(
        self,
        *,
        bar_specifications: list[str],
        start_date_time: datetime,
        end_date_time: datetime,
        tz_name: str,
        instrument_ids: list[InstrumentId],
        use_rth: bool,
        **kwargs: object,
    ) -> list[Bar]:
        self.bar_request = {
            "bar_specifications": bar_specifications,
            "start_date_time": start_date_time,
            "end_date_time": end_date_time,
            "tz_name": tz_name,
            "instrument_ids": instrument_ids,
            "use_rth": use_rth,
            "timeout": kwargs["timeout"],
        }
        return [make_bar(date(2026, 7, 14))]


def _source() -> IbkrHistoricalBarSource:
    """构造固定参数的数据源。"""
    return IbkrHistoricalBarSource(
        host="127.0.0.1",
        port=4002,
        client_id=1201,
        use_regular_trading_hours=True,
        request_timeout_seconds=120,
        log_level="ERROR",
    )


def test_adapter_requires_connection() -> None:
    """连接前不得发起合约或日线请求。"""
    source = _source()
    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(source.request_instruments([InstrumentId.from_str("SPY.ARCA")]))


def test_adapter_uses_native_historic_client_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配层应固定标准 Bar 参数、并幂等清理 NT 客户端。"""
    FakeHistoricClient.instances.clear()
    monkeypatch.setattr(
        ibkr_module,
        "HistoricInteractiveBrokersClient",
        FakeHistoricClient,
    )
    source = _source()

    asyncio.run(source.connect())
    asyncio.run(source.connect())
    client = FakeHistoricClient.instances[0]
    assert client.connect_count == 1
    assert client.kwargs["host"] == "127.0.0.1"
    assert client.kwargs["port"] == 4002
    assert client.kwargs["client_id"] == 1201

    instrument_id = InstrumentId.from_str("SPY.ARCA")
    instruments = asyncio.run(source.request_instruments([instrument_id]))
    bars = asyncio.run(
        source.request_daily_bars(
            instrument_id,
            datetime(2026, 7, 1),
            datetime(2026, 7, 15),
        ),
    )
    assert instruments[0].id == instrument_id
    assert str(bars[0].bar_type) == "SPY.ARCA-1-DAY-LAST-EXTERNAL"
    assert client.bar_request["bar_specifications"] == ["1-DAY-LAST"]
    assert client.bar_request["tz_name"] == "UTC"
    assert client.bar_request["use_rth"] is True
    assert client.bar_request["timeout"] == 120

    asyncio.run(source.close())
    asyncio.run(source.close())
    assert client._client.stop_count == 1
    assert client._client.dispose_count == 1


def test_adapter_does_not_stop_an_already_stopped_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NT 已停止时只执行释放。"""
    FakeHistoricClient.instances.clear()
    monkeypatch.setattr(ibkr_module, "HistoricInteractiveBrokersClient", FakeHistoricClient)
    source = _source()
    asyncio.run(source.connect())
    client = FakeHistoricClient.instances[0]
    client._client.is_stopped = True

    asyncio.run(source.close())
    assert client._client.stop_count == 0
    assert client._client.dispose_count == 1
