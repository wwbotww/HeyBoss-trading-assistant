"""NT BacktestExchange 使用的精确每股佣金模型。"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.common.config import NautilusConfig
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.orders import Order


class PerShareFeeModelConfig(NautilusConfig, frozen=True):
    """每股佣金配置。"""

    commission_per_share_usd: str


class PerShareFeeModel(FeeModel):  # type: ignore[misc]
    """先按股数计算总佣金。再交给 NT Money 按美分取整。"""

    def __init__(self, config: PerShareFeeModelConfig) -> None:
        super().__init__()
        self._commission_per_share = Decimal(config.commission_per_share_usd)
        if self._commission_per_share < 0:
            raise ValueError("commission_per_share_usd 不得为负数")

    def get_commission(
        self,
        order: Order,
        fill_qty: Quantity,
        fill_px: Price,
        instrument: Instrument,
    ) -> Money:
        """返回成交股数对应的 USD 总佣金。"""
        del order, fill_px
        commission = fill_qty.as_decimal() * self._commission_per_share
        return Money(commission, instrument.quote_currency)
