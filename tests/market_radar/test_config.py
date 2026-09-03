"""市场雷达配置测试。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from trading_assistant.market_radar.config import load_market_radar_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CONFIG = PROJECT_ROOT / "config" / "market-radar.yaml"


def _valid() -> dict[str, Any]:
    loaded = yaml.safe_load(PROJECT_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def _write(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_project_config_loads_price_universe_and_probe_contract() -> None:
    config = load_market_radar_config(PROJECT_CONFIG)
    assert config.benchmark == "SPY.US"
    assert config.equal_weight_benchmark == "RSP.US"
    assert config.credit_proxy == ("HYG.US", "LQD.US")
    assert config.index_membership_symbol == "GSPC.INDX"
    assert len(config.sector_etfs) == 11
    assert config.watchlist == (
        "AAPL.US",
        "MSFT.US",
        "NVDA.US",
        "GOOGL.US",
        "AMZN.US",
        "META.US",
        "JPM.US",
        "XOM.US",
        "JNJ.US",
        "TSLA.US",
    )
    assert len(config.monitor_instruments) == 15
    assert len(config.price_instrument_ids) == 25
    assert dict(config.watchlist_sectors) == {
        "AAPL.US": "information_technology",
        "MSFT.US": "information_technology",
        "NVDA.US": "information_technology",
        "GOOGL.US": "communication_services",
        "AMZN.US": "consumer_discretionary",
        "META.US": "communication_services",
        "JPM.US": "financials",
        "XOM.US": "energy",
        "JNJ.US": "health_care",
        "TSLA.US": "consumer_discretionary",
    }
    assert config.calendar_symbols == ("AAPL.US", "MSFT.US")
    assert config.fundamentals_symbols == ("AAPL.US", "JPM.US")
    assert config.vix_candidates == ("VIX.INDX",)
    assert config.vix3m_candidates == ("VIX3M.INDX",)


def _extra_root(value: dict[str, Any]) -> None:
    value["extra"] = {}


def _extra_market(value: dict[str, Any]) -> None:
    value["market"]["extra"] = 1


def _missing_probe_field(value: dict[str, Any]) -> None:
    value["probe"].pop("calendar_symbols")


def _missing_volatility_field(value: dict[str, Any]) -> None:
    value["probe"]["volatility_candidates"].pop("vix3m")


def _invalid_benchmark(value: dict[str, Any]) -> None:
    value["market"]["benchmark"] = "SPY"


def _benchmark_with_space(value: dict[str, Any]) -> None:
    value["market"]["benchmark"] = "SPY .US"


def _empty_watchlist(value: dict[str, Any]) -> None:
    value["watchlist"] = []


def _duplicate_watchlist(value: dict[str, Any]) -> None:
    value["watchlist"] = ["AAPL.US", "AAPL.US"]


def _missing_sector(value: dict[str, Any]) -> None:
    value["sector_etfs"].pop("energy")


def _mismatched_monitor_roles(value: dict[str, Any]) -> None:
    value["monitor_instruments"].pop()


def _probe_outside_watchlist(value: dict[str, Any]) -> None:
    value["probe"]["calendar_symbols"] = ["ORCL.US"]


def _mismatched_monitor_identity(value: dict[str, Any]) -> None:
    value["monitor_instruments"][0]["data_symbol"] = "QQQ.US"


def _missing_watchlist_sector(value: dict[str, Any]) -> None:
    value["watchlist_sectors"].pop("AAPL.US")


def _invalid_watchlist_sector(value: dict[str, Any]) -> None:
    value["watchlist_sectors"]["AAPL.US"] = "technology"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_extra_root, "root"),
        (_extra_market, "market"),
        (_missing_probe_field, "probe"),
        (_missing_volatility_field, "volatility_candidates"),
        (_invalid_benchmark, "市场后缀"),
        (_benchmark_with_space, "空白"),
        (_empty_watchlist, "非空列表"),
        (_duplicate_watchlist, "重复"),
        (_missing_sector, "11 个标准板块"),
        (_mismatched_monitor_roles, "价格监测角色"),
        (_probe_outside_watchlist, "watchlist"),
        (_mismatched_monitor_identity, "同一 EODHD 标的"),
        (_missing_watchlist_sector, "完全对应"),
        (_invalid_watchlist_sector, "标准板块"),
    ],
)
def test_invalid_config_fails_before_remote_requests(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    payload = _valid()
    mutate(payload)
    path = tmp_path / "market-radar.yaml"
    _write(path, payload)
    with pytest.raises(ValueError, match=message):
        load_market_radar_config(path)


@pytest.mark.parametrize("payload", [None, []])
def test_non_mapping_sections_are_rejected(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "market-radar.yaml"
    _write(path, payload)
    with pytest.raises(ValueError, match="映射"):
        load_market_radar_config(path)


def test_non_mapping_market_section_is_rejected(tmp_path: Path) -> None:
    payload = _valid()
    payload["market"] = []
    path = tmp_path / "market-radar.yaml"
    _write(path, payload)
    with pytest.raises(ValueError, match="映射"):
        load_market_radar_config(path)
