"""横截面因子分数到目标权重的纯函数。"""

from __future__ import annotations

import math
from collections.abc import Mapping


def calculate_factor_weights(
    scores: Mapping[str, float],
    *,
    top_n: int,
    target_gross_exposure: float,
) -> dict[str, float]:
    """选择稳定排序的最高分标的并等权分配目标敞口。"""
    if top_n < 1:
        raise ValueError("top_n must be positive")
    if not 0 < target_gross_exposure <= 1:
        raise ValueError("target_gross_exposure must be in (0, 1]")
    invalid = [instrument_id for instrument_id, score in scores.items() if not math.isfinite(score)]
    if invalid:
        raise ValueError(f"factor scores must be finite: {', '.join(sorted(invalid))}")
    selected = sorted(scores, key=lambda instrument_id: (-scores[instrument_id], instrument_id))[
        :top_n
    ]
    if len(selected) < top_n:
        return {}
    weight = target_gross_exposure / len(selected)
    return dict.fromkeys(selected, weight)
