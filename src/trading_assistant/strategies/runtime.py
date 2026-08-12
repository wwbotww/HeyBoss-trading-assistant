"""回测与实盘共用的单策略 NT Actor 装配边界。"""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import ImportableActorConfig

from trading_assistant.strategies.config import (
    ConfiguredStrategy,
    DualMomentumSettings,
    PatchTSTFactorSettings,
)


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
    factor_data_client_id: str = "FACTOR"
    allow_evaluation_predictions: bool = False


def build_strategy_actor(
    strategy: ConfiguredStrategy,
    context: StrategyRuntimeContext,
) -> ImportableActorConfig:
    """为本次唯一活动策略构建 NT 原生可导入 Actor 配置。"""
    settings = strategy.settings
    if strategy.name == "patchtst_e3" and isinstance(settings, PatchTSTFactorSettings):
        actor_config: dict[str, object] = {
            "instrument_ids": list(context.instrument_ids),
            "top_n": settings.top_n,
            "target_gross_exposure": settings.target_gross_exposure,
            "signal_expiry_hours": settings.signal_expiry_hours,
            "strategy_name": strategy.name,
            "database_url": context.database_url,
            "signal_scope": context.signal_scope,
            "stream_data": context.stream_bars,
            "bootstrap_from_catalog": context.bootstrap_from_catalog,
            "data_client_id": context.factor_data_client_id,
            "publish_after_ns": context.publish_after_ns,
            "allow_evaluation_predictions": (
                settings.allow_evaluation_predictions and context.allow_evaluation_predictions
            ),
        }
        if context.catalog_lookback_days > 0:
            actor_config["catalog_lookback_days"] = context.catalog_lookback_days
        return ImportableActorConfig(
            actor_path=("trading_assistant.strategies.patchtst_factor:PatchTSTFactorActor"),
            config_path=("trading_assistant.strategies.patchtst_factor:PatchTSTFactorActorConfig"),
            config=actor_config,
        )
    if strategy.name != "dual_momentum" or not isinstance(settings, DualMomentumSettings):
        raise ValueError(f"不支持的活动策略: {strategy.name}")
    actor_config = {
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
