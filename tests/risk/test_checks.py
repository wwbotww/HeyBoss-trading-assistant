"""风控纯计算测试。"""

from trading_assistant.risk.checks import (
    apply_weight_limits,
    effective_strategy_equity,
    order_rejection_reason,
)
from trading_assistant.risk.config import RiskLimits

LIMITS = RiskLimits(10_000.0, 5000.0, 0.25, 3, 0.80)


def test_strategy_equity_uses_lower_of_account_and_allocation() -> None:
    assert (
        effective_strategy_equity(
            account_equity_usd=1_000_000.0,
            strategy_capital_usd=10_000.0,
        )
        == 10_000.0
    )
    assert (
        effective_strategy_equity(
            account_equity_usd=8_000.0,
            strategy_capital_usd=10_000.0,
        )
        == 8_000.0
    )


def test_weight_limits_leave_unallocated_cash() -> None:
    assert apply_weight_limits({"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}, LIMITS) == {
        "A": 0.25,
        "B": 0.25,
        "C": 0.25,
    }
    assert apply_weight_limits({"BIL": 1.0}, LIMITS) == {"BIL": 0.25}


def test_total_gross_limit_scales_capped_weights() -> None:
    limits = RiskLimits(10_000.0, 5000.0, 0.50, 3, 0.60)
    assert apply_weight_limits({"A": 0.50, "B": 0.50}, limits) == {
        "A": 0.30,
        "B": 0.30,
    }


def test_order_checks_all_mvp_limits() -> None:
    def check(
        *,
        notional: float = 1000.0,
        weight: float = 0.20,
        gross: float = 0.60,
        new_positions: int = 1,
    ) -> str | None:
        return order_rejection_reason(
            notional_usd=notional,
            target_weight=weight,
            target_gross_exposure=gross,
            daily_new_positions=new_positions,
            limits=LIMITS,
        )

    assert check() is None
    assert check(notional=5000.01) is not None
    assert check(weight=0.26) is not None
    assert check(new_positions=4) is not None
    assert check(gross=0.81) is not None
