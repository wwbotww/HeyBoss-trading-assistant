"""NT MessageBus 上传递的交易领域事件。"""

from __future__ import annotations

from nautilus_trader.core.message import Event
from nautilus_trader.core.uuid import UUID4

TRADE_SIGNAL_TOPIC = "events.trade_signal.dual_momentum"


class TradeSignalEvent(Event):  # type: ignore[misc]
    """不可变的多标的目标权重事件。不作为行情 CustomData 使用。"""

    def __init__(
        self,
        *,
        strategy_name: str,
        target_weights: tuple[tuple[str, float], ...],
        rebalance_key: str,
        reason: str,
        expires_at_ns: int,
        ts_event: int,
        ts_init: int,
        event_id: UUID4 | None = None,
    ) -> None:
        self._id = event_id or UUID4()
        self._strategy_name = strategy_name
        self._target_weights = target_weights
        self._rebalance_key = rebalance_key
        self._reason = reason
        self._expires_at_ns = expires_at_ns
        self._ts_event = ts_event
        self._ts_init = ts_init
        self._locked = True

    def __setattr__(self, name: str, value: object) -> None:
        """初始化完成后拒绝字段修改。"""
        if getattr(self, "_locked", False):
            raise AttributeError("TradeSignalEvent is immutable")
        super().__setattr__(name, value)

    @property
    def id(self) -> UUID4:
        """返回事件 ID。"""
        return self._id

    @property
    def strategy_name(self) -> str:
        """返回产生事件的策略名。"""
        return self._strategy_name

    @property
    def target_weights(self) -> tuple[tuple[str, float], ...]:
        """返回按 instrument ID 排序的目标权重。"""
        return self._target_weights

    @property
    def rebalance_key(self) -> str:
        """返回策略调仓周期的稳定幂等键。"""
        return self._rebalance_key

    @property
    def reason(self) -> str:
        """返回信号依据摘要。"""
        return self._reason

    @property
    def expires_at_ns(self) -> int:
        """返回 UTC 纳秒过期时间。"""
        return self._expires_at_ns

    @property
    def ts_event(self) -> int:
        """返回事件发生时间。"""
        return self._ts_event

    @property
    def ts_init(self) -> int:
        """返回事件初始化时间。"""
        return self._ts_init
