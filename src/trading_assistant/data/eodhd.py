"""EODHD 日线响应到 NautilusTrader 原生对象的适配层。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from http.client import HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity, Instrument
from nautilus_trader.model.objects import Currency, Price, Quantity

from trading_assistant.data.config import InstrumentSpec

HttpTransport = Callable[[str, int], bytes]


def _download(url: str, timeout_seconds: int) -> bytes:
    """通过固定 HTTPS 端点下载响应; 不把含 token 的 URL 写入异常。"""
    request = Request(  # noqa: S310
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "HeyBoss-trading-assistant/0.1",
        },
        method="GET",
    )
    try:
        response = cast(HTTPResponse, urlopen(request, timeout=timeout_seconds))  # noqa: S310
        with response:
            return response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("EODHD authentication failed; check EODHD_API_TOKEN") from None
        if exc.code == 429 or exc.code >= 500:
            raise RuntimeError(f"EODHD temporary HTTP failure: status={exc.code}") from None
        raise ValueError(f"EODHD request was rejected: status={exc.code}") from None
    except URLError as exc:
        reason = type(exc.reason).__name__
        raise ConnectionError(f"EODHD connection failed: {reason}") from None


def _decimal_field(row: dict[str, object], field: str) -> Decimal:
    """读取有限十进制字段并拒绝 bool、null 和非数字值。"""
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"EODHD row has invalid {field!r}")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"EODHD row has invalid {field!r}") from exc
    if not result.is_finite():
        raise ValueError(f"EODHD row has non-finite {field!r}")
    return result


def _price(value: Decimal, precision: int) -> Price:
    """按标的精度构造 NT Price。"""
    return Price.from_str(f"{value:.{precision}f}")


class EodhdHistoricalBarSource:
    """使用 EODHD EOD API 生成总回报调整后的 NT 日线。"""

    def __init__(
        self,
        *,
        api_token: str,
        request_timeout_seconds: int,
        transport: HttpTransport = _download,
        base_url: str = "https://eodhd.com/api",
    ) -> None:
        token = api_token.strip()
        if not token:
            raise ValueError("EODHD_API_TOKEN is required for the EODHD data provider")
        if request_timeout_seconds < 1:
            raise ValueError("request_timeout_seconds must be positive")
        self._api_token = token
        self._request_timeout_seconds = request_timeout_seconds
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._connected = False

    async def connect(self) -> None:
        """初始化无状态 HTTP 数据源。"""
        self._connected = True

    def _require_connected(self) -> None:
        """在请求前校验生命周期。"""
        if not self._connected:
            raise RuntimeError("Historical data source is not connected")

    async def request_instruments(
        self,
        specs: Sequence[InstrumentSpec],
    ) -> list[Instrument]:
        """从受版本控制的最小元数据构造 NT 原生 Equity。"""
        self._require_connected()
        return [
            Equity(
                instrument_id=InstrumentId.from_str(spec.instrument_id),
                raw_symbol=Symbol(spec.symbol),
                currency=Currency.from_str(spec.currency),
                price_precision=spec.price_precision,
                price_increment=Price.from_str(spec.price_increment),
                lot_size=Quantity.from_int(spec.lot_size),
                ts_event=0,
                ts_init=0,
            )
            for spec in specs
        ]

    async def request_daily_bars(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """请求 EOD JSON; 把单一 adjusted_close 转成一致的调整后 OHLC。"""
        self._require_connected()
        query = urlencode(
            {
                "api_token": self._api_token,
                "fmt": "json",
                "period": "d",
                "order": "a",
                "from": start.date().isoformat(),
                "to": end.date().isoformat(),
            },
        )
        symbol = quote(spec.data_symbol, safe=".-")
        url = f"{self._base_url}/eod/{symbol}?{query}"
        payload = await asyncio.to_thread(
            self._transport,
            url,
            self._request_timeout_seconds,
        )
        return self._parse_bars(spec, payload, start=start, end=end)

    def _parse_bars(
        self,
        spec: InstrumentSpec,
        payload: bytes,
        *,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """严格校验响应结构并构造 NT Bar。"""
        try:
            decoded = cast(object, json.loads(payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("EODHD returned invalid JSON") from exc
        if not isinstance(decoded, list):
            raise ValueError("EODHD EOD response must be a JSON array")

        bar_type = BarType.from_str(f"{spec.instrument_id}-1-DAY-LAST-EXTERNAL")
        bars: list[Bar] = []
        for index, item in enumerate(decoded):
            if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
                raise ValueError(f"EODHD row {index} must be an object")
            row = cast(dict[str, object], item)
            date_value = row.get("date")
            if not isinstance(date_value, str):
                raise ValueError(f"EODHD row {index} has invalid 'date'")
            try:
                trading_day = datetime.strptime(date_value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise ValueError(f"EODHD row {index} has invalid 'date'") from exc
            if not start.date() <= trading_day <= end.date():
                continue

            raw_open = _decimal_field(row, "open")
            raw_high = _decimal_field(row, "high")
            raw_low = _decimal_field(row, "low")
            raw_close = _decimal_field(row, "close")
            adjusted_close = _decimal_field(row, "adjusted_close")
            volume = _decimal_field(row, "volume")
            if min(raw_open, raw_high, raw_low, raw_close, adjusted_close) <= 0:
                raise ValueError(f"EODHD row {index} contains a non-positive price")
            if volume < 0 or volume != volume.to_integral_value():
                raise ValueError(f"EODHD row {index} has invalid 'volume'")

            factor = adjusted_close / raw_close
            open_price = _price(raw_open * factor, spec.price_precision)
            close_price = _price(adjusted_close, spec.price_precision)
            high_price = max(
                _price(raw_high * factor, spec.price_precision),
                open_price,
                close_price,
            )
            low_price = min(
                _price(raw_low * factor, spec.price_precision),
                open_price,
                close_price,
            )
            event_time = datetime.combine(trading_day, time.min, tzinfo=UTC)
            ts_event = int(event_time.timestamp() * 1_000_000_000)
            ts_init = int((event_time + timedelta(days=1)).timestamp() * 1_000_000_000) - 1
            bars.append(
                Bar(
                    bar_type=bar_type,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=Quantity.from_int(int(volume)),
                    ts_event=ts_event,
                    ts_init=ts_init,
                ),
            )
        return bars

    async def close(self) -> None:
        """结束无状态 HTTP 数据源生命周期。"""
        self._connected = False
