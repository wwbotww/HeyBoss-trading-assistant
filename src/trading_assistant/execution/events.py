"""NT MessageBus 上传递的交易领域事件。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from nautilus_trader.core.message import Event
from nautilus_trader.core.uuid import UUID4

TRADE_SIGNAL_TOPIC = "events.trade_signal"


@dataclass(frozen=True)
class FactorContext:
    """随审批和恢复保留的因子依据。"""

    asof_date: str
    delivery_id: str
    model_release_id: str
    source_kind: str
    calendar_version: str
    candidate_ids: tuple[str, ...]
    eligible_count: int
    catalog_path: str

    def to_payload(self) -> dict[str, Any]:
        """转换为数据库可序列化的普通数据。"""
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FactorContext:
        """恢复必填上下文; 不替旧记录补造来源。"""
        return cls(
            asof_date=str(payload["asof_date"]),
            delivery_id=str(payload["delivery_id"]),
            model_release_id=str(payload["model_release_id"]),
            source_kind=str(payload["source_kind"]),
            calendar_version=str(payload["calendar_version"]),
            candidate_ids=tuple(str(value) for value in payload["candidate_ids"]),
            eligible_count=int(payload["eligible_count"]),
            catalog_path=str(payload["catalog_path"]),
        )


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
        preserve_positions: tuple[str, ...] = (),
        not_before_ns: int = 0,
        factor_context: FactorContext | None = None,
    ) -> None:
        self._id = event_id or UUID4()
        self._strategy_name = strategy_name
        self._target_weights = target_weights
        self._rebalance_key = rebalance_key
        self._reason = reason
        self._preserve_positions = preserve_positions
        self._not_before_ns = not_before_ns
        self._factor_context = factor_context
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
    def preserve_positions(self) -> tuple[str, ...]:
        """返回保持执行时数量的标的。"""
        return self._preserve_positions

    @property
    def not_before_ns(self) -> int:
        """返回最早可执行 UTC 纳秒。"""
        return self._not_before_ns

    @property
    def factor_context(self) -> FactorContext | None:
        """返回因子特有的交付及时间依据。"""
        return self._factor_context

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
