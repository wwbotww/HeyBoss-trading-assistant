"""市场雷达非敏感配置加载与校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml

from trading_assistant.data.config import InstrumentKind, InstrumentSpec

EXPECTED_SECTORS = frozenset(
    {
        "communication_services",
        "consumer_discretionary",
        "consumer_staples",
        "energy",
        "financials",
        "health_care",
        "industrials",
        "information_technology",
        "materials",
        "real_estate",
        "utilities",
    }
)


@dataclass(frozen=True)
class MarketRadarConfig:
    """价格监测宇宙与来源探测共用的非敏感配置。"""

    benchmark: str
    equal_weight_benchmark: str
    credit_proxy: tuple[str, str]
    vix: str
    vix3m: str
    index_membership_symbol: str
    sector_etfs: tuple[tuple[str, str], ...]
    watchlist: tuple[str, ...]
    watchlist_sectors: tuple[tuple[str, str], ...]
    monitor_instruments: tuple[InstrumentSpec, ...]
    calendar_symbols: tuple[str, ...]
    fundamentals_symbols: tuple[str, ...]

    @property
    def price_instrument_ids(self) -> tuple[str, ...]:
        """按基准、信用、板块、自选股的稳定顺序返回价格监测池。"""
        return (
            self.benchmark,
            self.equal_weight_benchmark,
            *self.credit_proxy,
            *(instrument_id for _, instrument_id in self.sector_etfs),
            *self.watchlist,
        )

    @property
    def macro_price_instrument_ids(self) -> tuple[str, ...]:
        """按信用代理和波动率期限结构顺序返回宏观价格输入。"""
        return (*self.credit_proxy, self.vix, self.vix3m)


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"配置项 {name!r} 必须是映射")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"配置项 {name!r} 字段不完整或包含未知字段")


def _symbol(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "." not in value:
        raise ValueError(f"配置项 {name!r} 必须是带市场后缀的非空标的 ID")
    result = value.strip()
    if any(character.isspace() for character in result):
        raise ValueError(f"配置项 {name!r} 不得包含空白字符")
    return result


def _symbols(value: object, *, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"配置项 {name!r} 必须是非空列表")
    result = tuple(_symbol(item, name=f"{name}[]") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"配置项 {name!r} 不得包含重复标的")
    return result


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"配置项 {name!r} 必须是非空字符串")
    result = value.strip()
    if any(character.isspace() for character in result):
        raise ValueError(f"配置项 {name!r} 不得包含空白字符")
    return result


def _date(value: object, *, name: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"配置项 {name!r} 必须是 ISO 日期") from exc
    raise ValueError(f"配置项 {name!r} 必须是 ISO 日期")


def _instrument_kind(value: object, *, name: str) -> InstrumentKind:
    if value not in {"equity", "index"}:
        raise ValueError(f"配置项 {name!r} 只允许 equity 或 index")
    return cast(InstrumentKind, value)


def _monitor_instruments(value: object) -> tuple[InstrumentSpec, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("配置项 'monitor_instruments' 必须是非空列表")
    expected = {
        "symbol",
        "instrument_id",
        "data_symbol",
        "instrument_kind",
        "primary_exchange",
        "first_trading_date",
    }
    instruments: list[InstrumentSpec] = []
    for index, item in enumerate(value):
        row = _mapping(item, name=f"monitor_instruments[{index}]")
        _exact_keys(row, expected, name=f"monitor_instruments[{index}]")
        symbol = _text(row["symbol"], name=f"monitor_instruments[{index}].symbol")
        instrument_id = _symbol(
            row["instrument_id"],
            name=f"monitor_instruments[{index}].instrument_id",
        )
        data_symbol = _symbol(
            row["data_symbol"],
            name=f"monitor_instruments[{index}].data_symbol",
        )
        if instrument_id != data_symbol or symbol != instrument_id.partition(".")[0]:
            raise ValueError(
                f"配置项 'monitor_instruments[{index}]' 的 symbol、instrument_id 与 "
                "data_symbol 必须表示同一 EODHD 标的"
            )
        instruments.append(
            InstrumentSpec(
                symbol=symbol,
                instrument_id=instrument_id,
                data_symbol=data_symbol,
                exchange="SMART",
                primary_exchange=_text(
                    row["primary_exchange"],
                    name=f"monitor_instruments[{index}].primary_exchange",
                ),
                currency="USD",
                price_precision=4,
                price_increment="0.0100",
                lot_size=1,
                first_trading_date=_date(
                    row["first_trading_date"],
                    name=f"monitor_instruments[{index}].first_trading_date",
                ),
                instrument_kind=_instrument_kind(
                    row["instrument_kind"],
                    name=f"monitor_instruments[{index}].instrument_kind",
                ),
            )
        )
    ids = tuple(spec.instrument_id for spec in instruments)
    if len(ids) != len(set(ids)):
        raise ValueError("配置项 'monitor_instruments' 不得包含重复标的")
    return tuple(instruments)


def _sector_etfs(value: object) -> tuple[tuple[str, str], ...]:
    mapping = _mapping(value, name="sector_etfs")
    if set(mapping) != EXPECTED_SECTORS:
        raise ValueError("配置项 'sector_etfs' 必须完整包含 11 个标准板块")
    result = tuple(
        (sector, _symbol(instrument_id, name=f"sector_etfs.{sector}"))
        for sector, instrument_id in mapping.items()
    )
    ids = tuple(instrument_id for _, instrument_id in result)
    if len(ids) != len(set(ids)):
        raise ValueError("配置项 'sector_etfs' 不得重复使用同一标的")
    return result


def _watchlist_sectors(
    value: object,
    *,
    watchlist: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    mapping = _mapping(value, name="watchlist_sectors")
    if set(mapping) != set(watchlist):
        raise ValueError("配置项 'watchlist_sectors' 必须与 watchlist 完全对应")
    result: list[tuple[str, str]] = []
    for instrument_id in watchlist:
        sector = _text(mapping[instrument_id], name=f"watchlist_sectors.{instrument_id}")
        if sector not in EXPECTED_SECTORS:
            raise ValueError(f"配置项 'watchlist_sectors.{instrument_id}' 不是标准板块")
        result.append((instrument_id, sector))
    return tuple(result)


def load_market_radar_config(path: Path) -> MarketRadarConfig:
    """加载市场雷达配置并在任何远端请求或本地写入前失败关闭。"""
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    root = _mapping(loaded, name="root")
    _exact_keys(
        root,
        {
            "market",
            "sector_etfs",
            "watchlist",
            "watchlist_sectors",
            "monitor_instruments",
            "probe",
        },
        name="root",
    )
    market = _mapping(root["market"], name="market")
    probe = _mapping(root["probe"], name="probe")
    volatility = _mapping(market.get("volatility"), name="volatility")
    _exact_keys(
        market,
        {
            "benchmark",
            "equal_weight_benchmark",
            "credit_proxy",
            "volatility",
            "index_membership_symbol",
        },
        name="market",
    )
    _exact_keys(
        probe,
        {"calendar_symbols", "fundamentals_symbols"},
        name="probe",
    )
    _exact_keys(volatility, {"vix", "vix3m"}, name="volatility")

    credit_proxy = _symbols(market["credit_proxy"], name="credit_proxy")
    if len(credit_proxy) != 2:
        raise ValueError("配置项 'credit_proxy' 必须恰好包含两个标的")
    sectors = _sector_etfs(root["sector_etfs"])
    watchlist = _symbols(root["watchlist"], name="watchlist")
    watchlist_sectors = _watchlist_sectors(
        root["watchlist_sectors"],
        watchlist=watchlist,
    )
    monitors = _monitor_instruments(root["monitor_instruments"])
    benchmark = _symbol(market["benchmark"], name="benchmark")
    equal_weight = _symbol(
        market["equal_weight_benchmark"],
        name="equal_weight_benchmark",
    )
    vix = _symbol(volatility["vix"], name="volatility.vix")
    vix3m = _symbol(volatility["vix3m"], name="volatility.vix3m")
    monitor_role_ids = (
        benchmark,
        equal_weight,
        *credit_proxy,
        vix,
        vix3m,
        *(instrument_id for _, instrument_id in sectors),
    )
    if len(monitor_role_ids) != len(set(monitor_role_ids)):
        raise ValueError("市场基准、信用代理与板块 ETF 不得重复使用同一标的")
    if set(monitor_role_ids) != {spec.instrument_id for spec in monitors}:
        raise ValueError("配置项 'monitor_instruments' 必须与全部价格监测角色完全对应")
    monitors_by_id = {spec.instrument_id: spec for spec in monitors}
    if any(
        monitors_by_id[instrument_id].instrument_kind != "index" for instrument_id in (vix, vix3m)
    ):
        raise ValueError("VIX 与 VIX3M 监测标的必须声明为 index")
    if any(
        spec.instrument_kind != "equity"
        for instrument_id, spec in monitors_by_id.items()
        if instrument_id not in {vix, vix3m}
    ):
        raise ValueError("除 VIX 与 VIX3M 外的固定监测标的必须声明为 equity")
    if set(watchlist) & set(monitor_role_ids):
        raise ValueError("配置项 'watchlist' 不得重复声明市场 ETF")

    calendar_symbols = _symbols(probe["calendar_symbols"], name="calendar_symbols")
    fundamentals_symbols = _symbols(
        probe["fundamentals_symbols"],
        name="fundamentals_symbols",
    )
    if not set(calendar_symbols + fundamentals_symbols) <= set(watchlist):
        raise ValueError("Calendar 与 Fundamentals 探测标的必须来自 watchlist")

    return MarketRadarConfig(
        benchmark=benchmark,
        equal_weight_benchmark=equal_weight,
        credit_proxy=(credit_proxy[0], credit_proxy[1]),
        vix=vix,
        vix3m=vix3m,
        index_membership_symbol=_symbol(
            market["index_membership_symbol"],
            name="index_membership_symbol",
        ),
        sector_etfs=sectors,
        watchlist=watchlist,
        watchlist_sectors=watchlist_sectors,
        monitor_instruments=monitors,
        calendar_symbols=calendar_symbols,
        fundamentals_symbols=fundamentals_symbols,
    )
