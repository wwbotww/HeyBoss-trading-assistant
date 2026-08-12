"""历史数据管道配置加载与校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

import yaml


@dataclass(frozen=True)
class InstrumentSpec:
    """供应商无关的标的与 NT Instrument 配置。"""

    symbol: str
    instrument_id: str
    data_symbol: str
    exchange: str
    primary_exchange: str
    currency: str
    price_precision: int
    price_increment: str
    lot_size: int
    live_instrument_id: str | None = None
    first_trading_date: date | None = None
    last_trading_date: date | None = None
    factor_security_id: str | None = None

    @property
    def canonical_id(self) -> str:
        """返回 Catalog、信号和回测共同使用的稳定标识。"""
        return self.instrument_id

    @property
    def resolved_live_instrument_id(self) -> str:
        """返回 IBKR 执行标识; 旧配置默认沿用规范标识。"""
        return self.live_instrument_id or self.instrument_id

    def effective_trading_interval(
        self,
        start: date,
        end: date | None,
    ) -> tuple[date, date | None] | None:
        """返回请求区间与显式交易生命周期的交集。"""
        effective_start = max(start, self.first_trading_date or start)
        effective_end = end
        if self.last_trading_date is not None:
            effective_end = (
                self.last_trading_date
                if effective_end is None
                else min(effective_end, self.last_trading_date)
            )
        if effective_end is not None and effective_start > effective_end:
            return None
        return effective_start, effective_end


HistoricalProvider = Literal["eodhd", "ibkr"]
PriceBasis = Literal["total_return_adjusted", "split_adjusted"]
RefreshMode = Literal["replace", "append"]


@dataclass(frozen=True)
class HistoricalDataConfig:
    """历史数据请求与增量同步参数。"""

    provider: HistoricalProvider
    price_basis: PriceBasis
    refresh_mode: RefreshMode
    history_years: int
    signal_bar_type_suffix: str
    execution_bar_type_suffix: str
    use_regular_trading_hours: bool
    request_window_days: int | None
    request_interval_seconds: float
    max_attempts: int
    retry_backoff_seconds: tuple[float, ...]
    live_sync_delay_minutes: int
    overlap_days: int
    request_timeout_seconds: int
    max_concurrent_requests: int


@dataclass(frozen=True)
class QualityConfig:
    """日线质量检查参数。"""

    max_absolute_daily_return: float
    stale_after_days: int


@dataclass(frozen=True)
class DataPipelineConfig:
    """历史数据管道完整配置。"""

    historical_data: HistoricalDataConfig
    quality: QualityConfig


def _load_mapping(path: Path) -> dict[str, Any]:
    """读取 YAML 并确保顶层是映射。"""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"配置文件顶层必须是映射: {path}")
    return loaded


def _required_mapping(parent: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    """读取必需的子映射。"""
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"配置项 {key!r} 必须是映射: {path}")
    return value


def _optional_date(value: object) -> date | None:
    """读取 YAML 原生日期或 ISO 日期字符串。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("expected ISO date")


def load_instruments(path: Path) -> tuple[InstrumentSpec, ...]:
    """从 YAML 加载并校验标的清单。"""
    root = _load_mapping(path)
    values = root.get("instruments")
    if not isinstance(values, list) or not values:
        raise ValueError(f"配置项 'instruments' 必须是非空列表: {path}")

    instruments: list[InstrumentSpec] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"instruments[{index}] 必须是映射: {path}")
        try:
            instrument = InstrumentSpec(
                symbol=str(value["symbol"]),
                instrument_id=str(value["instrument_id"]),
                data_symbol=str(value["data_symbol"]),
                exchange=str(value["exchange"]),
                primary_exchange=str(value["primary_exchange"]),
                currency=str(value["currency"]),
                price_precision=int(value["price_precision"]),
                price_increment=str(value["price_increment"]),
                lot_size=int(value["lot_size"]),
                live_instrument_id=(
                    None
                    if value.get("live_instrument_id") is None
                    else str(value["live_instrument_id"])
                ),
                first_trading_date=_optional_date(value.get("first_trading_date")),
                last_trading_date=_optional_date(value.get("last_trading_date")),
                factor_security_id=(
                    None
                    if value.get("factor_security_id") is None
                    else str(value["factor_security_id"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, KeyError):
                raise ValueError(
                    f"instruments[{index}] 缺少字段 {exc.args[0]!r}: {path}",
                ) from exc
            raise ValueError(f"instruments[{index}] 字段类型无效: {path}") from exc
        instruments.append(instrument)

        if instrument.price_precision < 0:
            raise ValueError(f"instruments[{index}].price_precision 不得为负数: {path}")
        try:
            price_increment = float(instrument.price_increment)
        except ValueError as exc:
            raise ValueError(
                f"instruments[{index}].price_increment 必须是正数: {path}",
            ) from exc
        if price_increment <= 0:
            raise ValueError(f"instruments[{index}].price_increment 必须是正数: {path}")
        if instrument.lot_size < 1:
            raise ValueError(f"instruments[{index}].lot_size 必须大于等于 1: {path}")
        if (
            instrument.first_trading_date is not None
            and instrument.last_trading_date is not None
            and instrument.first_trading_date > instrument.last_trading_date
        ):
            raise ValueError(
                f"instruments[{index}].first_trading_date 不得晚于 last_trading_date: {path}"
            )
        if instrument.factor_security_id is not None and not instrument.factor_security_id.strip():
            raise ValueError(f"instruments[{index}].factor_security_id 不得为空: {path}")

    instrument_ids = [instrument.instrument_id for instrument in instruments]
    if len(instrument_ids) != len(set(instrument_ids)):
        raise ValueError(f"instrument_id 不得重复: {path}")
    live_instrument_ids = [instrument.resolved_live_instrument_id for instrument in instruments]
    if len(live_instrument_ids) != len(set(live_instrument_ids)):
        raise ValueError(f"live_instrument_id 不得重复: {path}")
    factor_security_ids = [
        instrument.factor_security_id
        for instrument in instruments
        if instrument.factor_security_id is not None
    ]
    if len(factor_security_ids) != len(set(factor_security_ids)):
        raise ValueError(f"factor_security_id 不得重复: {path}")

    return tuple(instruments)


def load_data_config(path: Path) -> DataPipelineConfig:
    """从 YAML 加载并校验历史数据管道配置。"""
    root = _load_mapping(path)
    historical = _required_mapping(root, "historical_data", path)
    quality = _required_mapping(root, "quality", path)

    try:
        historical_config = HistoricalDataConfig(
            provider=cast(HistoricalProvider, str(historical["provider"])),
            price_basis=cast(PriceBasis, str(historical["price_basis"])),
            refresh_mode=cast(RefreshMode, str(historical["refresh_mode"])),
            history_years=int(historical["history_years"]),
            signal_bar_type_suffix=str(historical["signal_bar_type_suffix"]),
            execution_bar_type_suffix=str(historical["execution_bar_type_suffix"]),
            use_regular_trading_hours=bool(historical["use_regular_trading_hours"]),
            request_window_days=(
                None
                if historical["request_window_days"] is None
                else int(historical["request_window_days"])
            ),
            request_interval_seconds=float(historical["request_interval_seconds"]),
            max_attempts=int(historical["max_attempts"]),
            retry_backoff_seconds=tuple(
                float(value) for value in historical["retry_backoff_seconds"]
            ),
            live_sync_delay_minutes=int(historical["live_sync_delay_minutes"]),
            overlap_days=int(historical["overlap_days"]),
            request_timeout_seconds=int(historical["request_timeout_seconds"]),
            max_concurrent_requests=int(historical["max_concurrent_requests"]),
        )
        quality_config = QualityConfig(
            max_absolute_daily_return=float(quality["max_absolute_daily_return"]),
            stale_after_days=int(quality["stale_after_days"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"数据管道配置字段无效: {path}: {exc}") from exc

    if historical_config.history_years < 1:
        raise ValueError("history_years 必须大于等于 1")
    if historical_config.provider not in {"eodhd", "ibkr"}:
        raise ValueError("provider 只允许 eodhd 或 ibkr")
    if historical_config.price_basis not in {"total_return_adjusted", "split_adjusted"}:
        raise ValueError("price_basis 只允许 total_return_adjusted 或 split_adjusted")
    if historical_config.refresh_mode not in {"replace", "append"}:
        raise ValueError("refresh_mode 只允许 replace 或 append")
    if historical_config.provider == "eodhd" and (
        historical_config.price_basis != "total_return_adjusted"
        or historical_config.refresh_mode != "replace"
    ):
        raise ValueError("EODHD 必须使用 total_return_adjusted 与 replace")
    if historical_config.provider == "ibkr" and (
        historical_config.price_basis != "split_adjusted"
        or historical_config.refresh_mode != "append"
    ):
        raise ValueError("IBKR 必须使用 split_adjusted 与 append")
    if historical_config.execution_bar_type_suffix != "1-DAY-LAST-EXTERNAL":
        raise ValueError(
            "execution_bar_type_suffix 必须为 1-DAY-LAST-EXTERNAL",
        )
    if historical_config.provider == "eodhd" and (
        historical_config.signal_bar_type_suffix != "1-DAY-LAST-INTERNAL"
    ):
        raise ValueError("EODHD signal_bar_type_suffix 必须为 1-DAY-LAST-INTERNAL")
    if historical_config.provider == "ibkr" and (
        historical_config.signal_bar_type_suffix != historical_config.execution_bar_type_suffix
    ):
        raise ValueError("IBKR 备用源的 signal/execution BarType 必须一致")
    if (
        historical_config.request_window_days is not None
        and historical_config.request_window_days < 1
    ):
        raise ValueError("request_window_days 必须为 null 或大于等于 1")
    if historical_config.request_interval_seconds < 0:
        raise ValueError("request_interval_seconds 不得为负数")
    if historical_config.max_attempts < 1:
        raise ValueError("max_attempts 必须大于等于 1")
    if len(historical_config.retry_backoff_seconds) < historical_config.max_attempts - 1:
        raise ValueError("retry_backoff_seconds 数量不足以覆盖全部重试")
    if any(delay < 0 for delay in historical_config.retry_backoff_seconds):
        raise ValueError("retry_backoff_seconds 不得包含负数")
    if historical_config.overlap_days < 0:
        raise ValueError("overlap_days 不得为负数")
    if historical_config.request_timeout_seconds < 1:
        raise ValueError("request_timeout_seconds 必须大于等于 1")
    if historical_config.max_concurrent_requests < 1:
        raise ValueError("max_concurrent_requests 必须大于等于 1")
    if not 0 < quality_config.max_absolute_daily_return <= 1:
        raise ValueError("max_absolute_daily_return 必须在 (0, 1] 范围内")
    if quality_config.stale_after_days < 1:
        raise ValueError("stale_after_days 必须大于等于 1")

    return DataPipelineConfig(historical_data=historical_config, quality=quality_config)
