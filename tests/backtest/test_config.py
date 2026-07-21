"""M2 YAML 配置校验测试。"""

from pathlib import Path

import pytest
import yaml

from trading_assistant.backtest.config import load_backtest_settings
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.strategies.config import load_dual_momentum_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_loads_project_m2_configs() -> None:
    backtest = load_backtest_settings(
        PROJECT_ROOT / "config" / "backtest.yaml", project_root=PROJECT_ROOT
    )
    risk = load_risk_limits(PROJECT_ROOT / "config" / "risk.yaml")
    strategy = load_dual_momentum_settings(PROJECT_ROOT / "config" / "strategies.yaml")
    assert backtest.starting_balance_usd == 10000
    assert backtest.bar_availability_delay_ns > 0
    assert risk.strategy_capital_usd == 10000
    assert risk.max_gross_exposure == 0.80
    assert strategy.lookback_months == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"starting_balance_usd": 0},
        {"commission_per_share_usd": -1},
        {"slippage_ticks": 2},
        {"trading_days_per_year": 0},
        {"bar_availability_delay_ns": 0},
    ],
)
def test_rejects_invalid_backtest_values(tmp_path: Path, overrides: dict[str, object]) -> None:
    values = {
        "starting_balance_usd": 10000,
        "commission_per_share_usd": 0.005,
        "slippage_ticks": 1,
        "bar_availability_delay_ns": 86399999999999,
        "trading_days_per_year": 252,
        "risk_free_rate": 0,
        "report_root": "reports",
        **overrides,
    }
    path = tmp_path / "backtest.yaml"
    path.write_text(yaml.safe_dump({"backtest": values}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"必须|不得|只支持"):
        load_backtest_settings(path, project_root=tmp_path)


def test_rejects_missing_config_mappings(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="必须是映射"):
        load_backtest_settings(path, project_root=tmp_path)
    with pytest.raises(ValueError, match="必须是映射"):
        load_risk_limits(path)
    with pytest.raises(ValueError, match="必须是映射"):
        load_dual_momentum_settings(path)
