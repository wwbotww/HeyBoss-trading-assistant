"""双动量目标权重纯函数。"""

from __future__ import annotations

import pandas as pd


def calculate_dual_momentum_weights(
    monthly_closes: pd.DataFrame,
    *,
    lookback_months: int,
    top_n: int,
    fallback_instrument: str,
) -> dict[str, float]:
    """按完整日历月价格动量计算未经风控裁剪的目标权重。

    DataFrame 行索引必须可转换为月度时间。列名为 instrument ID。兜底标的不参与
    排名。没有足够有效候选数据时返回空映射并保持现金。
    """
    if lookback_months < 1:
        raise ValueError("lookback_months 必须大于等于 1")
    if top_n < 1:
        raise ValueError("top_n 必须大于等于 1")
    if monthly_closes.empty:
        return {}

    closes = monthly_closes.copy()
    closes.index = pd.PeriodIndex(closes.index, freq="M")
    closes = closes.sort_index()
    closes = closes[~closes.index.duplicated(keep="last")]

    current_month = closes.index[-1]
    reference_month = current_month - lookback_months
    if reference_month not in closes.index:
        return {}

    current = closes.loc[current_month]
    reference = closes.loc[reference_month]
    candidate_ids = sorted(
        str(column) for column in closes.columns if str(column) != fallback_instrument
    )
    momentum: list[tuple[str, float]] = []
    for instrument_id in candidate_ids:
        current_price = current.get(instrument_id)
        reference_price = reference.get(instrument_id)
        if pd.isna(current_price) or pd.isna(reference_price):
            continue
        current_value = float(current_price)
        reference_value = float(reference_price)
        if current_value <= 0 or reference_value <= 0:
            continue
        momentum.append((instrument_id, current_value / reference_value - 1.0))

    if not momentum:
        return {}

    positive = [item for item in momentum if item[1] > 0]
    positive.sort(key=lambda item: (-item[1], item[0]))
    selected = positive[:top_n]
    if selected:
        weight = 1.0 / len(selected)
        return {instrument_id: weight for instrument_id, _ in selected}

    fallback_price = current.get(fallback_instrument)
    if fallback_price is None or pd.isna(fallback_price) or float(fallback_price) <= 0:
        return {}
    return {fallback_instrument: 1.0}
