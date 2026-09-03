"""EODHD 到 NT 原生对象的适配测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from trading_assistant.data import eodhd as eodhd_module
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.eodhd import EodhdHistoricalBarSource


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        "SPY",
        "SPY.US",
        "SPY.US",
        "SMART",
        "ARCA",
        "USD",
        4,
        "0.0100",
        1,
        "SPY.ARCA",
    )


def _payload() -> bytes:
    return json.dumps(
        [
            {
                "date": "2026-07-14",
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 100,
                "adjusted_close": 95,
                "volume": 123456,
            },
        ],
    ).encode()


def _route_payload(
    url: str,
    *,
    eod: bytes,
    splits: bytes = b"[]",
    dividends: bytes = b"[]",
) -> bytes:
    """按 EODHD 端点返回测试响应。"""
    if "/splits/" in url:
        return splits
    if "/div/" in url:
        return dividends
    return eod


def test_adapter_builds_native_instrument_and_total_return_bars() -> None:
    """适配器应同时生成拆股执行价与总回报信号价。"""
    requests: list[tuple[str, int]] = []

    def transport(url: str, timeout: int) -> bytes:
        requests.append((url, timeout))
        return _route_payload(
            url,
            eod=_payload(),
            splits=b'[{"date":"2026-07-20","split":"2/1"}]',
            dividends=(
                b'[{"date":"2026-07-10","value":0.5,"unadjusted_value":1.0,"currency":"USD"}]'
            ),
        )

    source = EodhdHistoricalBarSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=30,
        transport=transport,
    )
    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(source.request_instruments([_spec()]))

    asyncio.run(source.connect())
    instruments = asyncio.run(source.request_instruments([_spec()]))
    bars = asyncio.run(
        source.request_daily_bars(
            _spec(),
            datetime(2026, 7, 1),
            datetime(2026, 7, 31),
        ),
    )

    assert instruments[0].id.value == "SPY.US"
    assert instruments[0].price_increment.as_double() == 0.01
    assert len(bars) == 2
    assert str(bars[0].bar_type) == "SPY.US-1-DAY-LAST-EXTERNAL"
    assert bars[0].open.as_double() == 50
    assert bars[0].high.as_double() == 55
    assert bars[0].low.as_double() == 45
    assert bars[0].close.as_double() == 50
    assert bars[0].volume.as_double() == 123456
    assert str(bars[1].bar_type) == "SPY.US-1-DAY-LAST-INTERNAL"
    assert bars[1].open.as_double() == 95
    assert bars[1].high.as_double() == 104.5
    assert bars[1].low.as_double() == 85.5
    assert bars[1].close.as_double() == 95
    assert bars[0].ts_event < bars[0].ts_init < bars[1].ts_init
    assert any("/eod/SPY.US?" in url and "order=a" in url for url, _ in requests)
    assert any("/splits/SPY.US?" in url for url, _ in requests)
    assert any("/div/SPY.US?" in url for url, _ in requests)
    assert all("api_token=test-token" in url and timeout == 30 for url, timeout in requests)

    asyncio.run(source.close())
    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(
            source.request_daily_bars(
                _spec(),
                datetime(2026, 7, 1),
                datetime(2026, 7, 31),
            ),
        )


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b'{"code": 401}',
        b'[{"date": "bad"}]',
        b'[{"date":"2026-07-14","open":1,"high":1,"low":1,"close":0,'
        b'"adjusted_close":1,"volume":1}]',
        b'[{"date":"2026-07-14","open":1,"high":1,"low":1,"close":1,'
        b'"adjusted_close":1,"volume":1.5}]',
    ],
)
def test_adapter_rejects_malformed_responses(payload: bytes) -> None:
    """错误响应不得产生部分或猜测出来的行情。"""
    source = EodhdHistoricalBarSource(
        api_token="test-token",  # noqa: S106
        request_timeout_seconds=30,
        transport=lambda _url, _timeout: payload,
    )
    asyncio.run(source.connect())
    with pytest.raises(ValueError, match="EODHD"):
        asyncio.run(
            source.request_daily_bars(
                _spec(),
                datetime(2026, 7, 1),
                datetime(2026, 7, 31),
            ),
        )


def test_adapter_requires_token() -> None:
    """空 token 必须在发起网络请求前失败。"""
    with pytest.raises(ValueError, match="EODHD_API_TOKEN"):
        EodhdHistoricalBarSource(api_token=" ", request_timeout_seconds=30)  # noqa: S106


def test_adapter_validates_timeout_fields_and_requested_dates() -> None:
    """构造参数、非有限字段和请求日期范围都必须严格处理。"""
    with pytest.raises(ValueError, match="timeout"):
        EodhdHistoricalBarSource(api_token="token", request_timeout_seconds=0)  # noqa: S106

    with pytest.raises(ValueError, match="invalid"):
        eodhd_module._decimal_field({"value": True}, "value")
    with pytest.raises(ValueError, match="non-finite"):
        eodhd_module._decimal_field({"value": "NaN"}, "value")

    outside_payload = _payload().replace(b"2026-07-14", b"2026-06-14")
    source = EodhdHistoricalBarSource(
        api_token="token",  # noqa: S106
        request_timeout_seconds=30,
        transport=lambda url, _timeout: _route_payload(url, eod=outside_payload),
    )
    asyncio.run(source.connect())
    bars = asyncio.run(
        source.request_daily_bars(
            _spec(),
            datetime(2026, 7, 1),
            datetime(2026, 7, 31),
        ),
    )
    assert bars == []
