"""独立只读市场监测域。"""

from trading_assistant.market_radar.capabilities import (
    CapabilityReport,
    CapabilityResult,
    run_capability_checks,
    write_capability_report,
)
from trading_assistant.market_radar.config import MarketRadarConfig, load_market_radar_config
from trading_assistant.market_radar.macro import (
    RiskAppetiteComponents,
    RiskAppetitePoint,
    RiskAppetiteSnapshot,
    calculate_risk_appetite_snapshot,
)
from trading_assistant.market_radar.membership import (
    CurrentMarketMember,
    CurrentMarketMembership,
    CurrentMarketMembershipSource,
)
from trading_assistant.market_radar.metrics import (
    BreadthMetric,
    CurrentBreadthSnapshot,
    MarketPriceMetrics,
    MetricValue,
    PriceBar,
    PriceCoverage,
    PriceRadarSnapshot,
    SectorPriceMetrics,
    StockPriceMetrics,
    calculate_current_breadth_snapshot,
    calculate_price_snapshot,
)
from trading_assistant.market_radar.prices import (
    build_macro_price_instrument_specs,
    build_membership_instrument_specs,
    build_price_instrument_specs,
    load_internal_price_bars,
)
from trading_assistant.market_radar.service import (
    MarketBreadthSyncMode,
    MarketBreadthSyncSummary,
    MarketMacroSyncSummary,
    MarketRadarPriceSyncSummary,
    MarketRadarSyncMode,
    sync_current_market_breadth,
    sync_market_macro,
    sync_market_radar_prices,
)
from trading_assistant.market_radar.storage import MarketRadarRepository, MarketRadarSyncRun

__all__ = [
    "BreadthMetric",
    "CapabilityReport",
    "CapabilityResult",
    "CurrentBreadthSnapshot",
    "CurrentMarketMember",
    "CurrentMarketMembership",
    "CurrentMarketMembershipSource",
    "MarketBreadthSyncMode",
    "MarketBreadthSyncSummary",
    "MarketMacroSyncSummary",
    "MarketPriceMetrics",
    "MarketRadarConfig",
    "MarketRadarPriceSyncSummary",
    "MarketRadarRepository",
    "MarketRadarSyncMode",
    "MarketRadarSyncRun",
    "MetricValue",
    "PriceBar",
    "PriceCoverage",
    "PriceRadarSnapshot",
    "RiskAppetiteComponents",
    "RiskAppetitePoint",
    "RiskAppetiteSnapshot",
    "SectorPriceMetrics",
    "StockPriceMetrics",
    "build_macro_price_instrument_specs",
    "build_membership_instrument_specs",
    "build_price_instrument_specs",
    "calculate_current_breadth_snapshot",
    "calculate_price_snapshot",
    "calculate_risk_appetite_snapshot",
    "load_internal_price_bars",
    "load_market_radar_config",
    "run_capability_checks",
    "sync_current_market_breadth",
    "sync_market_macro",
    "sync_market_radar_prices",
    "write_capability_report",
]
