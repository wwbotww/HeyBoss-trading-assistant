"""横截面因子分数到目标权重的纯函数。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FactorDecision:
    """完整候选集的因子决策; 缺分保持集合不依赖账户状态。"""

    status: Literal["REBALANCE", "SKIP"]
    target_weights: tuple[tuple[str, float], ...]
    preserve_positions: tuple[str, ...]
    candidate_count: int
    eligible_count: int
    reason: str


def calculate_factor_weights(
    scores: Mapping[str, float | None],
    *,
    top_n: int,
    target_gross_exposure: float,
    max_unscorable_fraction: float,
) -> FactorDecision:
    """选择稳定排序的最高分标的并等权分配目标敞口。"""
    if top_n < 1:
        raise ValueError("top_n must be positive")
    if not 0 < target_gross_exposure <= 1:
        raise ValueError("target_gross_exposure must be in (0, 1]")
    if not 0 <= max_unscorable_fraction <= 1:
        raise ValueError("max_unscorable_fraction must be in [0, 1]")
    eligible = {key: value for key, value in scores.items() if value is not None}
    preserve = tuple(sorted(set(scores) - eligible.keys()))
    invalid = [key for key, value in eligible.items() if not math.isfinite(value)]
    if invalid:
        raise ValueError(f"factor scores must be finite: {', '.join(sorted(invalid))}")
    selected = sorted(
        eligible, key=lambda instrument_id: (-eligible[instrument_id], instrument_id)
    )[:top_n]
    reason = ""
    if len(selected) < top_n:
        reason = "insufficient_eligible_scores"
    elif len(preserve) / len(scores) > max_unscorable_fraction:
        reason = "unscorable_fraction_exceeded"
    if reason:
        return FactorDecision("SKIP", (), preserve, len(scores), len(eligible), reason)
    weight = target_gross_exposure / len(selected)
    return FactorDecision(
        "REBALANCE",
        tuple(sorted((key, weight) for key in selected)),
        preserve,
        len(scores),
        len(eligible),
        "eligible_top_n",
    )
