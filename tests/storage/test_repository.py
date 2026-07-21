"""SQLite 审计仓储测试。"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.storage.models import (
    AccountSnapshotRecord,
    ApprovalRecord,
    BacktestRunRecord,
    SignalRecord,
    SignalWorkflowRecord,
)
from trading_assistant.storage.repository import PositionSnapshotInput, TradingRepository


def test_records_signal_approval_and_run(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/audit.db")
    repository.create_schema()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key="2026-06",
        reason="test",
        expires_at_ns=20,
        ts_event=10,
        ts_init=10,
    )
    repository.record_signal(event)
    repository.record_approval(
        event,
        approval_mode="auto",
        decision="APPROVED",
        reason="auto approval",
        timestamp_ns=11,
    )
    now = datetime.now(UTC)
    repository.start_backtest_run("run-1", now)
    repository.complete_backtest_run(
        "run-1", completed_at=now, status="COMPLETED", summary={"fills": 0}
    )

    assert repository.count(SignalRecord) == 1
    assert repository.count(ApprovalRecord) == 1
    assert repository.count(BacktestRunRecord) == 1
    repository.close()


def test_signal_workflow_is_idempotent_and_transitions_atomically(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/workflow.db")
    repository.create_schema()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key="2026-06",
        reason="test",
        expires_at_ns=20_000_000_000,
        ts_event=10_000_000_000,
        ts_init=10_000_000_000,
    )

    workflow, created = repository.register_signal_workflow(event, scope="paper:DU123")
    duplicate, duplicate_created = repository.register_signal_workflow(
        TradeSignalEvent(
            strategy_name="dual_momentum",
            target_weights=(("QQQ.NASDAQ", 0.25),),
            rebalance_key="2026-06",
            reason="duplicate",
            expires_at_ns=30_000_000_000,
            ts_event=11_000_000_000,
            ts_init=11_000_000_000,
        ),
        scope="paper:DU123",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.event_id == workflow.event_id
    assert duplicate.target_weights == (("SPY.ARCA", 0.25),)
    assert repository.count(SignalWorkflowRecord) == 1

    assert repository.prepare_manual_approval(
        workflow.event_id,
        planned_orders=({"instrument_id": "SPY.ARCA", "side": "BUY", "quantity": 1},),
        risk_summary="risk passed",
        timestamp_ns=12_000_000_000,
    )
    assert not repository.prepare_manual_approval(
        workflow.event_id,
        planned_orders=(),
        risk_summary="stale writer",
        timestamp_ns=13_000_000_000,
    )
    assert repository.decide_manual_approval(
        workflow.event_id,
        approved=True,
        decision_by="42",
        timestamp_ns=14_000_000_000,
    )

    claimed = repository.claim_next_approved(timestamp_ns=15_000_000_000)
    assert claimed is not None
    assert claimed.event_id == workflow.event_id
    assert claimed.status == "PROCESSING"
    assert repository.claim_next_approved(timestamp_ns=15_000_000_001) is None
    assert repository.finish_processing(
        workflow.event_id,
        status="ORDERS_SUBMITTED",
        timestamp_ns=16_000_000_000,
    )
    assert repository.get_signal_workflow(workflow.event_id).status == "ORDERS_SUBMITTED"  # type: ignore[union-attr]
    repository.close()


def test_delivery_deduplication_and_missing_run_fail_closed(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/edges.db")
    repository.create_schema()
    assert repository.mark_telegram_delivered(
        source_key="order:1",
        chat_id="42",
        message_id="10",
        timestamp_ns=1,
    )
    assert not repository.mark_telegram_delivered(
        source_key="order:1",
        chat_id="42",
        message_id="11",
        timestamp_ns=2,
    )
    with pytest.raises(LookupError, match="not found"):
        repository.complete_backtest_run(
            "missing",
            completed_at=datetime.now(UTC),
            status="FAILED",
            summary={},
        )
    repository.close()


def test_risk_rejection_can_be_rearmed_once_with_a_new_alert_attempt(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/rearm.db")
    repository.create_schema()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key="2026-06",
        reason="momentum",
        expires_at_ns=20_000_000_000,
        ts_event=10_000_000_000,
        ts_init=10_000_000_000,
    )
    workflow, _ = repository.register_signal_workflow(event, scope="paper:DU123")
    assert repository.reject_new_signal(
        workflow.event_id,
        status="RISK_REJECTED",
        timestamp_ns=11_000_000_000,
        risk_summary="notional exceeded",
    )
    first_alert = repository.list_workflow_alert_notifications(scope="paper:DU123")[0]
    assert first_alert.source_key == f"workflow:{workflow.event_id}:RISK_REJECTED"
    assert repository.mark_telegram_delivered(
        source_key=first_alert.source_key,
        chat_id="42",
        message_id="1",
        timestamp_ns=12_000_000_000,
    )

    assert repository.rearm_terminal_signal(
        workflow.event_id,
        reason="capital allocation updated",
        timestamp_ns=13_000_000_000,
        expires_at_ns=30_000_000_000,
    )
    assert repository.get_signal_workflow(workflow.event_id).status == "NEW"  # type: ignore[union-attr]
    assert not repository.rearm_terminal_signal(
        workflow.event_id,
        reason="stale retry",
        timestamp_ns=14_000_000_000,
        expires_at_ns=30_000_000_000,
    )
    assert repository.reject_new_signal(
        workflow.event_id,
        status="RISK_REJECTED",
        timestamp_ns=15_000_000_000,
        risk_summary="still rejected",
    )
    second_alert = repository.list_workflow_alert_notifications(scope="paper:DU123")[0]
    assert second_alert.source_key == (f"workflow:{workflow.event_id}:RISK_REJECTED:attempt:1")
    assert repository.count(ApprovalRecord) == 1
    repository.close()


def test_expired_signal_can_be_explicitly_rearmed(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/expired-rearm.db")
    repository.create_schema()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key="2026-06",
        reason="momentum",
        expires_at_ns=20_000_000_000,
        ts_event=10_000_000_000,
        ts_init=10_000_000_000,
    )
    workflow, _ = repository.register_signal_workflow(event, scope="paper:DU123")
    assert repository.reject_new_signal(
        workflow.event_id,
        status="EXPIRED",
        timestamp_ns=21_000_000_000,
        risk_summary="signal expired",
    )
    assert repository.rearm_terminal_signal(
        workflow.event_id,
        reason="operator requested a fresh approval window",
        timestamp_ns=22_000_000_000,
        expires_at_ns=30_000_000_000,
    )
    restored = repository.get_signal_workflow(workflow.event_id)
    assert restored is not None
    assert restored.status == "NEW"
    assert restored.expires_at_utc.timestamp() == 30
    assert repository.count(ApprovalRecord) == 1
    repository.close()


def test_backtest_trade_ids_are_unique_per_run(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/fills.db")
    repository.create_schema()
    now = datetime.now(UTC)
    for run_id in ("run-1", "run-2"):
        repository.start_backtest_run(run_id, now)
        repository.record_fill(
            run_id=run_id,
            signal_event_id=f"signal-{run_id}",
            trade_id="deterministic-trade-id",
            timestamp_ns=1,
            strategy_name="dual_momentum",
            instrument_id="SPY.ARCA",
            client_order_id="O-1",
            direction="BUY",
            quantity=1,
            price=100,
            commission=0.01,
        )
    assert repository.list_fills("run-1")[0].trade_id == "run-1:deterministic-trade-id"
    assert repository.list_fills("run-2")[0].trade_id == "run-2:deterministic-trade-id"
    repository.close()


def test_portfolio_snapshot_and_dashboard_audits_are_readable(tmp_path: Path) -> None:
    repository = TradingRepository(f"sqlite:///{tmp_path}/dashboard.db")
    repository.create_schema()
    repository.record_portfolio_snapshot(
        timestamp_ns=10_000_000_000,
        account_id="IB-DU123",
        currency="USD",
        net_liquidation=10_500.0,
        free_cash=7_500.0,
        locked_cash=3_000.0,
        positions=(
            PositionSnapshotInput(
                instrument_id="SPY.ARCA",
                signed_quantity=5.0,
                side="LONG",
                avg_open_price=600.0,
                realized_pnl=12.5,
            ),
        ),
    )
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.5),),
        rebalance_key="2026-06",
        reason="momentum",
        expires_at_ns=20_000_000_000,
        ts_event=10_000_000_000,
        ts_init=10_000_000_000,
    )
    workflow, _ = repository.register_signal_workflow(event, scope="paper:DU123")
    repository.record_signal(event)
    repository.record_approval(
        event,
        approval_mode="manual",
        decision="RISK_REJECTED",
        reason="notional exceeded",
        timestamp_ns=11_000_000_000,
    )
    repository.record_order_event(
        signal_event_id=workflow.event_id,
        order_event_id="order-event-1",
        timestamp_ns=12_000_000_000,
        strategy_name="dual_momentum",
        instrument_id="SPY.ARCA",
        client_order_id="O-1",
        status="REJECTED",
        direction="BUY",
        quantity=5.0,
        reason="IBKR code 201",
    )

    snapshot = repository.latest_portfolio_snapshot(account_id="IB-DU123")
    assert snapshot is not None
    assert snapshot.net_liquidation == 10_500.0
    assert snapshot.positions[0].instrument_id == "SPY.ARCA"
    assert repository.list_signal_reviews(scope="paper:DU123")[0].target_weight == 0.5
    assert repository.list_decision_audits(scope="paper:DU123")[0].decision == "RISK_REJECTED"
    assert repository.list_order_audits(scope="paper:DU123")[0].reason == "IBKR code 201"
    assert repository.count(AccountSnapshotRecord) == 1
    repository.close()


def test_read_only_repository_rejects_writes(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/readonly.db"
    writer = TradingRepository(database_url)
    writer.create_schema()
    writer.close()
    reader = TradingRepository(database_url, read_only=True)

    with pytest.raises(OperationalError, match="readonly"):
        reader.record_portfolio_snapshot(
            timestamp_ns=1,
            account_id="IB-DU123",
            currency="USD",
            net_liquidation=1.0,
            free_cash=1.0,
            locked_cash=0.0,
            positions=(),
        )

    reader.close()
