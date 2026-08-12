"""唯一活动策略的 NT Actor 装配测试。"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from trading_assistant.strategies.config import (
    ConfiguredStrategy,
    DualMomentumSettings,
    PatchTSTFactorSettings,
    load_active_strategy,
)
from trading_assistant.strategies.runtime import StrategyRuntimeContext, build_strategy_actor

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _context() -> StrategyRuntimeContext:
    return StrategyRuntimeContext(
        instrument_ids=("SPY.US", "BIL.US"),
        signal_bar_types=(
            "SPY.US-1-DAY-LAST-INTERNAL",
            "BIL.US-1-DAY-LAST-INTERNAL",
        ),
        database_url="sqlite:///:memory:",
        signal_scope="test",
        stream_bars=False,
        bootstrap_from_catalog=True,
        bootstrap_bar_types=("SPY.US-1-DAY-LAST-EXTERNAL",),
        catalog_lookback_days=500,
        publish_after_ns=123,
    )


def test_builds_configured_dual_momentum_actor() -> None:
    strategy = load_active_strategy(PROJECT_ROOT / "config" / "strategies.yaml")

    actor = build_strategy_actor(strategy, _context())

    assert actor.actor_path.endswith(":DualMomentumActor")
    assert actor.config_path.endswith(":DualMomentumActorConfig")
    assert actor.config["strategy_name"] == "dual_momentum"
    assert actor.config["instrument_ids"] == ["SPY.US", "BIL.US"]
    assert actor.config["bootstrap_from_catalog"] is True
    assert actor.config["catalog_lookback_days"] == 500
    assert actor.config["publish_after_ns"] == 123


def test_rejects_unimplemented_strategy_at_runtime_boundary() -> None:
    strategy = ConfiguredStrategy(
        name="unknown",
        settings=DualMomentumSettings(
            approval_mode="manual",
            signal_expiry_hours=4,
            lookback_months=6,
            top_n=3,
            rebalance_frequency="month_end",
            fallback_instrument="BIL.US",
        ),
    )

    with pytest.raises(ValueError, match="不支持"):
        build_strategy_actor(strategy, _context())


def test_loads_and_builds_patchtst_factor_actor(tmp_path: Path) -> None:
    path = tmp_path / "strategies.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "active_strategy": "patchtst_e3",
                "strategies": {
                    "patchtst_e3": {
                        "approval_mode": "manual",
                        "signal_expiry_hours": 24,
                        "parameters": {
                            "top_n": 3,
                            "target_gross_exposure": 0.75,
                            "rebalance_frequency": "daily",
                            "allow_evaluation_predictions": True,
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    strategy = load_active_strategy(path)
    assert isinstance(strategy.settings, PatchTSTFactorSettings)
    context = replace(
        _context(),
        stream_bars=True,
        bootstrap_from_catalog=False,
        allow_evaluation_predictions=True,
    )
    actor = build_strategy_actor(strategy, context)
    assert actor.actor_path.endswith(":PatchTSTFactorActor")
    assert actor.config["stream_data"] is True
    assert actor.config["data_client_id"] == "FACTOR"
    assert actor.config["target_gross_exposure"] == 0.75
    assert actor.config["allow_evaluation_predictions"] is True


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"top_n": 0}, "top_n"),
        ({"target_gross_exposure": 0}, "target_gross_exposure"),
        ({"rebalance_frequency": "month_end"}, "daily"),
        ({"allow_evaluation_predictions": "false"}, "布尔值"),
    ],
)
def test_rejects_invalid_patchtst_strategy(
    tmp_path: Path,
    override: dict[str, object],
    message: str,
) -> None:
    parameters: dict[str, object] = {
        "top_n": 3,
        "target_gross_exposure": 0.75,
        "rebalance_frequency": "daily",
        "allow_evaluation_predictions": False,
    }
    parameters.update(override)
    path = tmp_path / "strategies.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "active_strategy": "patchtst_e3",
                "strategies": {
                    "patchtst_e3": {
                        "approval_mode": "manual",
                        "signal_expiry_hours": 24,
                        "parameters": parameters,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=message):
        load_active_strategy(path)
