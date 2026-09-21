"""交易活动查询测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_assistant.application.models import QuerySourceError, ResourceNotFoundError
from trading_assistant.application.trading_activity import TradingActivityQueryService
from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.storage.repository import TradingRepository


def _seed(
    tmp_path: Path, *, final_status: str = "FILLED", filled_quantity: float = 2
) -> tuple[str, str]:
    database_url = f"sqlite:///{tmp_path}/live.db"
    repository = TradingRepository(database_url)
    repository.create_schema()
    event = TradeSignalEvent(
        strategy_name="patchtst_e3",
        target_weights=(("AAPL.US", 0.25), ("MSFT.US", 0.25)),
        rebalance_key="2026-08-12",
        reason="factor rank",
        expires_at_ns=30_000_000_000,
        ts_event=10_000_000_000,
        ts_init=10_000_000_000,
    )
    workflow, _ = repository.register_signal_workflow(event, scope="paper:DU123")
    repository.record_signal(event)
    repository.prepare_manual_approval(
        workflow.event_id,
        planned_orders=({"instrument_id": "AAPL.US", "side": "BUY", "quantity": 2},),
        risk_summary="risk passed",
        timestamp_ns=11_000_000_000,
    )
    repository.record_approval(
        event,
        approval_mode="manual",
        decision="APPROVED",
        reason="operator approved",
        timestamp_ns=12_000_000_000,
    )
    repository.record_order_event(
        signal_event_id=workflow.event_id,
        order_event_id="oe-1",
        timestamp_ns=13_000_000_000,
        strategy_name="patchtst_e3",
        instrument_id="AAPL.US",
        client_order_id="O-1",
        status="SUBMITTED",
        direction="BUY",
        quantity=2,
        reason="submitted",
    )
    repository.record_order_event(
        signal_event_id=workflow.event_id,
        order_event_id="oe-2",
        timestamp_ns=14_000_000_000,
        strategy_name="patchtst_e3",
        instrument_id="AAPL.US",
        client_order_id="O-1",
        status=final_status,
        direction="BUY",
        quantity=2,
        reason="filled",
    )
    if filled_quantity:
        repository.record_fill(
            run_id=None,
            signal_event_id=workflow.event_id,
            trade_id="T-1",
            timestamp_ns=14_000_000_000,
            strategy_name="patchtst_e3",
            instrument_id="AAPL.US",
            client_order_id="O-1",
            direction="BUY",
            quantity=filled_quantity,
            price=100,
            commission=0.5,
        )
    repository.close()
    return database_url, workflow.event_id


def test_trading_views_link_signal_workflow_order_and_fill(tmp_path: Path) -> None:
    database_url, event_id = _seed(tmp_path)
    repository = TradingRepository(database_url, read_only=True)
    service = TradingActivityQueryService(
        repository=repository,
        scope="paper:DU123",
    )

    signals = service.list_signals(
        offset=0,
        limit=1,
        status="PENDING",
        instrument_id=None,
    )
    assert signals.has_more is True
    assert signals.items[0].status == "PENDING"
    assert (
        service.list_signals(
            offset=0,
            limit=10,
            status=None,
            instrument_id="MSFT.US",
        )
        .items[0]
        .target_weight
        == 0.25
    )

    workflows = service.list_workflows(offset=0, limit=10, status="PENDING")
    assert workflows.items[0].planned_order_count == 1
    assert service.list_workflows(offset=0, limit=10, status="DENIED").items == ()

    detail = service.workflow_detail(event_id)
    assert detail.target_weights == (("AAPL.US", 0.25), ("MSFT.US", 0.25))
    assert detail.planned_orders[0]["quantity"] == 2
    assert [item.kind for item in detail.timeline] == [
        "signal",
        "decision",
        "order",
        "fill",
        "order",
    ]
    assert detail.fills[0].trade_id == "T-1"

    orders = service.list_orders(
        offset=0,
        limit=10,
        status="FILLED",
        instrument_id="AAPL.US",
    )
    assert orders.items[0].event_count == 2
    assert orders.items[0].fill_count == 1
    assert orders.items[0].filled_quantity == 2
    assert (
        service.list_orders(
            offset=0,
            limit=10,
            status="REJECTED",
            instrument_id=None,
        ).items
        == ()
    )
    order = service.order_detail("O-1")
    assert [item.status for item in order.events] == ["SUBMITTED", "FILLED"]
    assert order.fills[0].price == 100

    fills = service.list_fills(offset=0, limit=10, instrument_id="AAPL.US")
    assert fills.items[0].commission == 0.5
    assert service.list_fills(offset=0, limit=10, instrument_id="MSFT.US").items == ()
    counts, latest_workflow, latest_fill = service.overview_activity()
    assert counts == {"PENDING": 1}
    assert latest_workflow is not None
    assert latest_fill is not None
    repository.close()


def test_trading_service_handles_missing_scope_objects_and_corrupt_database(
    tmp_path: Path,
) -> None:
    service = TradingActivityQueryService(repository=None, scope=None)
    assert service.list_workflows(offset=0, limit=10, status=None).items == ()
    assert (
        service.list_orders(
            offset=0,
            limit=10,
            status=None,
            instrument_id=None,
        ).items
        == ()
    )
    assert service.list_fills(offset=0, limit=10, instrument_id=None).items == ()
    assert service.overview_activity() == ({}, None, None)
    with pytest.raises(ResourceNotFoundError, match="不可用"):
        service.workflow_detail("missing")

    database_url, _ = _seed(tmp_path)
    reader = TradingRepository(database_url, read_only=True)
    scoped = TradingActivityQueryService(repository=reader, scope="paper:OTHER")
    with pytest.raises(ResourceNotFoundError, match="不存在"):
        scoped.workflow_detail("missing")
    with pytest.raises(ResourceNotFoundError, match="订单不存在"):
        scoped.order_detail("missing")
    reader.close()

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    broken = TradingRepository(f"sqlite:///{corrupt}", read_only=True)
    broken_service = TradingActivityQueryService(repository=broken, scope="paper:DU123")
    with pytest.raises(QuerySourceError, match="交易审计库"):
        broken_service.list_workflows(offset=0, limit=10, status=None)
    broken.close()


@pytest.mark.parametrize(
    ("final_status", "filled_quantity"),
    [
        ("FILLED", 2),
        ("PARTIALLY_FILLED", 1),
        ("CANCELED", 1),
        ("EXPIRED", 1),
        ("REJECTED", 0),
        ("DENIED", 0),
    ],
)
def test_order_views_do_not_regress_on_later_accepted_timestamp(
    tmp_path: Path, final_status: str, filled_quantity: float
) -> None:
    database_url, event_id = _seed(
        tmp_path, final_status=final_status, filled_quantity=filled_quantity
    )
    repository = TradingRepository(database_url)
    # 券商成交只有秒精度; 接单本地时钟和迟到回报不能覆盖已确定的状态。
    repository.record_order_event(
        signal_event_id=event_id,
        order_event_id="oe-late-accepted",
        timestamp_ns=14_650_000_000,
        strategy_name="patchtst_e3",
        instrument_id="AAPL.US",
        client_order_id="O-1",
        status="ACCEPTED",
        direction="BUY",
        quantity=2,
        reason="accepted report with finer timestamp",
    )
    service = TradingActivityQueryService(repository=repository, scope="paper:DU123")
    for status in (None, final_status):
        orders = service.list_orders(offset=0, limit=1, status=status, instrument_id=None)
        assert len(orders.items) == 1
        assert orders.items[0].status == final_status
        assert orders.items[0].filled_quantity == filled_quantity
    assert service.list_orders(offset=0, limit=1, status="ACCEPTED", instrument_id=None).items == ()
    detail = service.order_detail("O-1")
    assert detail.summary.status == final_status
    assert detail.events[-1].status == "ACCEPTED"
    repository.close()
