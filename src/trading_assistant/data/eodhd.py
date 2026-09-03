"""EODHD 日线响应到 NautilusTrader 原生对象的适配层。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import quote

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity, Instrument
from nautilus_trader.model.objects import Currency, Price, Quantity

from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.corporate_actions import (
    CorporateActions,
    DividendAction,
    SplitAction,
)
from trading_assistant.data.eodhd_http import EodhdHttpClient, HttpTransport, download


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


def _date_field(row: dict[str, object], index: int) -> datetime:
    """读取 EODHD ISO 日期。"""
    value = row.get("date")
    if not isinstance(value, str):
        raise ValueError(f"EODHD row {index} has invalid 'date'")
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"EODHD row {index} has invalid 'date'") from exc


def _split_ratio(value: object, index: int) -> Decimal:
    """解析 EODHD 的 `4/1` 或 `4:1` 拆股比例。"""
    if not isinstance(value, str):
        raise ValueError(f"EODHD split row {index} has invalid 'split'")
    separator = "/" if "/" in value else ":"
    parts = value.split(separator)
    if len(parts) != 2:
        raise ValueError(f"EODHD split row {index} has invalid 'split'")
    try:
        numerator = Decimal(parts[0])
        denominator = Decimal(parts[1])
    except InvalidOperation as exc:
        raise ValueError(f"EODHD split row {index} has invalid 'split'") from exc
    if not numerator.is_finite() or not denominator.is_finite() or min(numerator, denominator) <= 0:
        raise ValueError(f"EODHD split row {index} has invalid 'split'")
    return numerator / denominator


class EodhdHistoricalBarSource:
    """使用 EODHD EOD API 生成总回报调整后的 NT 日线。"""

    def __init__(
        self,
        *,
        api_token: str,
        request_timeout_seconds: int,
        max_concurrent_requests: int = 1,
        transport: HttpTransport = download,
        base_url: str = "https://eodhd.com/api",
    ) -> None:
        self._http = EodhdHttpClient(
            api_token=api_token,
            request_timeout_seconds=request_timeout_seconds,
            max_concurrent_requests=max_concurrent_requests,
            transport=transport,
            base_url=base_url,
        )
        self._actions: dict[str, CorporateActions] = {}
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
        actions = self._actions.get(spec.instrument_id)
        if actions is None:
            actions = await self.request_corporate_actions(spec, start, end)
        symbol = quote(spec.data_symbol, safe=".-")
        payload = await self._http.request_bytes(
            f"eod/{symbol}",
            {
                "fmt": "json",
                "period": "d",
                "order": "a",
                "from": start.date().isoformat(),
                "to": end.date().isoformat(),
            },
        )
        return self._parse_bars(spec, payload, actions=actions, start=start, end=end)

    async def request_corporate_actions(
        self,
        spec: InstrumentSpec,
        start: datetime,
        end: datetime,
    ) -> CorporateActions:
        """请求完整拆股和现金分红, 并缓存供拆股调整 OHLC 使用。"""
        self._require_connected()
        symbol = quote(spec.data_symbol, safe=".-")
        query = {
            "fmt": "json",
            "from": start.date().isoformat(),
            "to": end.date().isoformat(),
        }
        split_payload, dividend_payload = await asyncio.gather(
            self._http.request_bytes(f"splits/{symbol}", query),
            self._http.request_bytes(f"div/{symbol}", query),
        )
        actions = CorporateActions(
            instrument_id=spec.instrument_id,
            dividends=self._parse_dividends(
                spec,
                dividend_payload,
                start=start,
                end=end,
            ),
            splits=self._parse_splits(split_payload, start=start, end=end),
        )
        self._actions[spec.instrument_id] = actions
        return actions

    def _parse_bars(
        self,
        spec: InstrumentSpec,
        payload: bytes,
        *,
        actions: CorporateActions,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """严格校验响应并构造 signal INTERNAL 与 execution EXTERNAL Bar。"""
        try:
            decoded = cast(object, json.loads(payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("EODHD returned invalid JSON") from exc
        if not isinstance(decoded, list):
            raise ValueError("EODHD EOD response must be a JSON array")

        signal_bar_type = BarType.from_str(f"{spec.instrument_id}-1-DAY-LAST-INTERNAL")
        execution_bar_type = BarType.from_str(f"{spec.instrument_id}-1-DAY-LAST-EXTERNAL")
        bars: list[Bar] = []
        for index, item in enumerate(decoded):
            if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
                raise ValueError(f"EODHD row {index} must be an object")
            row = cast(dict[str, object], item)
            trading_day = _date_field(row, index).date()
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

            total_return_factor = adjusted_close / raw_close
            signal_open = _price(raw_open * total_return_factor, spec.price_precision)
            signal_close = _price(adjusted_close, spec.price_precision)
            signal_high = max(
                _price(raw_high * total_return_factor, spec.price_precision),
                signal_open,
                signal_close,
            )
            signal_low = min(
                _price(raw_low * total_return_factor, spec.price_precision),
                signal_open,
                signal_close,
            )
            split_factor = self._split_price_factor(trading_day, actions.splits)
            execution_open = _price(raw_open * split_factor, spec.price_precision)
            execution_close = _price(raw_close * split_factor, spec.price_precision)
            execution_high = max(
                _price(raw_high * split_factor, spec.price_precision),
                execution_open,
                execution_close,
            )
            execution_low = min(
                _price(raw_low * split_factor, spec.price_precision),
                execution_open,
                execution_close,
            )
            event_time = datetime.combine(trading_day, time.min, tzinfo=UTC)
            ts_event = int(event_time.timestamp() * 1_000_000_000)
            session_end = int((event_time + timedelta(days=1)).timestamp() * 1_000_000_000)
            bars.extend(
                (
                    Bar(
                        bar_type=execution_bar_type,
                        open=execution_open,
                        high=execution_high,
                        low=execution_low,
                        close=execution_close,
                        volume=Quantity.from_int(int(volume)),
                        ts_event=ts_event,
                        ts_init=session_end - 2,
                    ),
                    Bar(
                        bar_type=signal_bar_type,
                        open=signal_open,
                        high=signal_high,
                        low=signal_low,
                        close=signal_close,
                        volume=Quantity.from_int(int(volume)),
                        ts_event=ts_event,
                        ts_init=session_end - 1,
                    ),
                )
            )
        return bars

    @staticmethod
    def _split_price_factor(trading_day: date, splits: tuple[SplitAction, ...]) -> Decimal:
        """把原始历史价格转换为当前拆股口径。"""
        factor = Decimal(1)
        for split in splits:
            if split.ex_date > trading_day:
                factor /= split.ratio
        return factor

    def _parse_splits(
        self,
        payload: bytes,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[SplitAction, ...]:
        """解析 EODHD splits JSON。"""
        decoded = self._json_array(payload, "splits")
        actions: list[SplitAction] = []
        for index, item in enumerate(decoded):
            row = self._object_row(item, index, "split")
            ex_date = _date_field(row, index).date()
            if start.date() <= ex_date <= end.date():
                actions.append(
                    SplitAction(
                        ex_date=ex_date,
                        ratio=_split_ratio(row.get("split"), index),
                    )
                )
        return tuple(sorted(actions, key=lambda item: item.ex_date))

    def _parse_dividends(
        self,
        spec: InstrumentSpec,
        payload: bytes,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[DividendAction, ...]:
        """解析拆股调整后的每股分红值。"""
        decoded = self._json_array(payload, "dividends")
        actions: list[DividendAction] = []
        for index, item in enumerate(decoded):
            row = self._object_row(item, index, "dividend")
            ex_date = _date_field(row, index).date()
            if not start.date() <= ex_date <= end.date():
                continue
            value = _decimal_field(row, "value")
            unadjusted_raw = row.get("unadjusted_value", row.get("unadjustedValue"))
            unadjusted = (
                None
                if unadjusted_raw is None
                else _decimal_field({"value": unadjusted_raw}, "value")
            )
            currency = row.get("currency", spec.currency)
            if value < 0 or not isinstance(currency, str) or not currency:
                raise ValueError(f"EODHD dividend row {index} has invalid fields")
            actions.append(
                DividendAction(
                    ex_date=ex_date,
                    value=value,
                    unadjusted_value=unadjusted,
                    currency=currency,
                )
            )
        return tuple(sorted(actions, key=lambda item: item.ex_date))

    @staticmethod
    def _json_array(payload: bytes, label: str) -> list[object]:
        try:
            decoded = cast(object, json.loads(payload))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"EODHD returned invalid {label} JSON") from exc
        if not isinstance(decoded, list):
            raise ValueError(f"EODHD {label} response must be a JSON array")
        return cast(list[object], decoded)

    @staticmethod
    def _object_row(item: object, index: int, label: str) -> dict[str, object]:
        if not isinstance(item, dict) or not all(isinstance(key, str) for key in item):
            raise ValueError(f"EODHD {label} row {index} must be an object")
        return cast(dict[str, object], item)

    async def close(self) -> None:
        """结束无状态 HTTP 数据源生命周期。"""
        self._actions.clear()
        self._connected = False
