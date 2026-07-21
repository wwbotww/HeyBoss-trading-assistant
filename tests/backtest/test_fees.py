"""精确每股佣金模型测试。"""

from typing import cast

import pytest
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from trading_assistant.backtest.fees import PerShareFeeModel, PerShareFeeModelConfig


def test_multiplies_before_usd_rounding() -> None:
    model = PerShareFeeModel(PerShareFeeModelConfig(commission_per_share_usd="0.005"))
    instrument = TestInstrumentProvider.equity()
    commission = model.get_commission(
        cast("Order", object()),
        Quantity.from_int(5),
        Price.from_str("100.00"),
        instrument,
    )
    assert commission.as_double() == pytest.approx(0.03)


def test_rejects_negative_commission() -> None:
    with pytest.raises(ValueError, match="不得为负数"):
        PerShareFeeModel(PerShareFeeModelConfig(commission_per_share_usd="-0.005"))
