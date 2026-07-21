"""统一执行网关决策与持久化审批链路测试。"""

import time
from pathlib import Path
from typing import cast

import pytest
from nautilus_trader.common.component import TestClock, TimeEvent

from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.execution.gateway import (
    ExecutionGatewayConfig,
    ExecutionGatewayStrategy,
    _PlannedOrder,
    gateway_decision,
)
from trading_assistant.storage.repository import TradingRepository


class _GatewayHarness(ExecutionGatewayStrategy):
    def __init__(self, config: ExecutionGatewayConfig) -> None:
        super().__init__(config)
        self.rejection: str | None = None
        self.executed: list[str] = []
        self.test_clock = TestClock()
        self.test_clock.set_time(time.time_ns())

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    def _build_plan(
        self,
        event: TradeSignalEvent,
    ) -> tuple[tuple[_PlannedOrder, ...], str | None]:
        return (), self.rejection

    def _execute_plan(
        self,
        event: TradeSignalEvent,
        plan: tuple[_PlannedOrder, ...],
    ) -> None:
        self.executed.append(str(event.id))


def _gateway(tmp_path: Path, *, approval_mode: str) -> tuple[_GatewayHarness, TradingRepository]:
    repository = TradingRepository(f"sqlite:///{tmp_path}/gateway.db")
    repository.create_schema()
    gateway = _GatewayHarness(
        ExecutionGatewayConfig(
            instrument_ids=("SPY.ARCA",),
            bar_type_suffix="1-DAY-LAST-EXTERNAL",
            approval_mode=approval_mode,
            database_url=f"sqlite:///{tmp_path}/gateway.db",
            strategy_capital_usd=10_000,
            max_order_notional_usd=100_000,
            max_instrument_weight=0.25,
            max_daily_new_positions=3,
            max_gross_exposure=0.8,
        )
    )
    gateway._repository = repository
    return gateway, repository


def _event(repository: TradingRepository, *, expires_at_ns: int | None = None) -> TradeSignalEvent:
    now_ns = time.time_ns()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key=str(now_ns),
        reason="momentum",
        expires_at_ns=expires_at_ns or now_ns + 3_600_000_000_000,
        ts_event=now_ns,
        ts_init=now_ns,
    )
    repository.register_signal_workflow(event, scope="paper:DU123")
    return event


def test_expired_and_risk_rejected_never_execute() -> None:
    assert gateway_decision(approval_mode="auto", expired=True, risk_rejection=None) == "EXPIRED"
    assert (
        gateway_decision(approval_mode="auto", expired=False, risk_rejection="risk")
        == "RISK_REJECTED"
    )


def test_manual_waits_and_auto_executes_after_risk() -> None:
    assert gateway_decision(approval_mode="manual", expired=False, risk_rejection=None) == "PENDING"
    assert gateway_decision(approval_mode="auto", expired=False, risk_rejection=None) == "APPROVED"


def test_unknown_approval_mode_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported approval mode"):
        gateway_decision(approval_mode="invalid", expired=False, risk_rejection=None)


def test_manual_signal_never_executes_before_approval(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="manual")
    event = _event(repository)
    gateway._handle_signal(event)
    assert repository.get_signal_workflow(str(event.id)).status == "PENDING"  # type: ignore[union-attr]
    assert gateway.executed == []

    repository.decide_manual_approval(
        str(event.id),
        approved=True,
        decision_by="42",
        timestamp_ns=time.time_ns(),
    )
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert repository.get_signal_workflow(str(event.id)).status == "ORDERS_SUBMITTED"  # type: ignore[union-attr]
    assert gateway.executed == [str(event.id)]
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert gateway.executed == [str(event.id)]
    repository.close()


def test_manual_second_risk_check_can_reject(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="manual")
    event = _event(repository)
    gateway._handle_signal(event)
    repository.decide_manual_approval(
        str(event.id),
        approved=True,
        decision_by="42",
        timestamp_ns=time.time_ns(),
    )
    gateway.rejection = "price moved beyond risk limit"
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert repository.get_signal_workflow(str(event.id)).status == "RISK_REJECTED"  # type: ignore[union-attr]
    assert gateway.executed == []
    repository.close()


def test_denied_manual_signal_is_not_claimed(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="manual")
    event = _event(repository)
    gateway._handle_signal(event)
    repository.decide_manual_approval(
        str(event.id),
        approved=False,
        decision_by="42",
        timestamp_ns=time.time_ns(),
    )
    gateway._poll_approved_signal(cast(TimeEvent, object()))
    assert repository.get_signal_workflow(str(event.id)).status == "DENIED"  # type: ignore[union-attr]
    assert gateway.executed == []
    repository.close()


def test_auto_claims_once_and_initial_failures_never_execute(tmp_path: Path) -> None:
    gateway, repository = _gateway(tmp_path, approval_mode="auto")
    event = _event(repository)
    gateway._handle_signal(event)
    gateway._handle_signal(event)
    assert repository.get_signal_workflow(str(event.id)).status == "ORDERS_SUBMITTED"  # type: ignore[union-attr]
    assert gateway.executed == [str(event.id)]

    rejected = _event(repository)
    gateway.rejection = "risk"
    gateway._handle_signal(rejected)
    assert repository.get_signal_workflow(str(rejected.id)).status == "RISK_REJECTED"  # type: ignore[union-attr]

    expired = _event(repository, expires_at_ns=time.time_ns() - 1)
    gateway.rejection = None
    gateway._handle_signal(expired)
    assert repository.get_signal_workflow(str(expired.id)).status == "EXPIRED"  # type: ignore[union-attr]
    assert gateway.executed == [str(event.id)]
    repository.close()
