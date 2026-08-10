"""回测与实盘共用的单策略 NT Actor 装配边界。"""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import ImportableActorConfig

from trading_assistant.strategies.config import ConfiguredStrategy


@dataclass(frozen=True)
class StrategyRuntimeContext:
    """由运行环境注入、与具体策略参数无关的 Actor 配置。"""

    instrument_ids: tuple[str, ...]
    signal_bar_types: tuple[str, ...]
    database_url: str
    signal_scope: str
    stream_bars: bool
    bootstrap_from_catalog: bool
    bootstrap_bar_types: tuple[str, ...]
    catalog_lookback_days: int
    publish_after_ns: int


def build_strategy_actor(
    strategy: ConfiguredStrategy,
    context: StrategyRuntimeContext,
) -> ImportableActorConfig:
    """为本次唯一活动策略构建 NT 原生可导入 Actor 配置。"""
    if strategy.name != "dual_momentum":
        raise ValueError(f"不支持的活动策略: {strategy.name}")
    settings = strategy.settings
    actor_config: dict[str, object] = {
        "bar_types": list(context.signal_bar_types),
        "instrument_ids": list(context.instrument_ids),
        "lookback_months": settings.lookback_months,
        "top_n": settings.top_n,
        "fallback_instrument": settings.fallback_instrument,
        "signal_expiry_hours": settings.signal_expiry_hours,
        "strategy_name": strategy.name,
        "database_url": context.database_url,
        "signal_scope": context.signal_scope,
        "stream_bars": context.stream_bars,
        "bootstrap_from_catalog": context.bootstrap_from_catalog,
        "bootstrap_bar_types": list(context.bootstrap_bar_types),
        "publish_after_ns": context.publish_after_ns,
    }
    if context.catalog_lookback_days > 0:
        actor_config["catalog_lookback_days"] = context.catalog_lookback_days
    return ImportableActorConfig(
        actor_path="trading_assistant.strategies.dual_momentum:DualMomentumActor",
        config_path="trading_assistant.strategies.dual_momentum:DualMomentumActorConfig",
        config=actor_config,
    )
