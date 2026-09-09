"""PatchTST 因子 Actor 批次、幂等和 bootstrap 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from nautilus_trader.common.component import TestClock
from nautilus_trader.core.uuid import UUID4
from sqlalchemy import create_engine, func, select

from trading_assistant.data.factor import FactorScoreData
from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.storage.models import SignalRecord
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.patchtst_factor import (
    PatchTSTFactorActor,
    PatchTSTFactorActorConfig,
)


class _ActorHarness(PatchTSTFactorActor):
    def __init__(self, config: PatchTSTFactorActorConfig) -> None:
        super().__init__(config)
        self.test_clock = TestClock()
        self.published: list[tuple[FactorScoreData, ...]] = []

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    def _publish_signal(self, batch: tuple[FactorScoreData, ...]) -> None:
        self.published.append(batch)


class _PublishingActor(PatchTSTFactorActor):
    def __init__(self, config: PatchTSTFactorActorConfig) -> None:
        super().__init__(config)
        self.test_clock = TestClock()
        self.events: list[TradeSignalEvent] = []

    @property
    def clock(self) -> TestClock:
        return self.test_clock

    @property
    def msgbus(self) -> _MessageBus:
        return _MessageBus(self.events)


class _MessageBus:
    def __init__(self, events: list[TradeSignalEvent]) -> None:
        self._events = events

    def publish(self, topic: str, event: object) -> None:
        assert topic == "events.trade_signal"
        assert isinstance(event, TradeSignalEvent)
        self._events.append(event)


def _actor(
    tmp_path: Path,
    *,
    allow_evaluation: bool = False,
    instrument_ids: tuple[str, ...] = ("AAPL.US", "MSFT.US"),
) -> _ActorHarness:
    return _ActorHarness(
        PatchTSTFactorActorConfig(
            instrument_ids=instrument_ids,
            top_n=1,
            target_gross_exposure=0.25,
            signal_expiry_hours=24,
            database_url=f"sqlite:///{tmp_path}/factor.db",
            stream_data=False,
            allow_evaluation_predictions=allow_evaluation,
        )
    )


def _row(
    canonical_id: str,
    *,
    batch_id: str = "delivery:2025-01-02",
    batch_size: int = 2,
    score: float = 1.0,
    source_kind: str = "signal_inference",
    security_id: str | None = None,
    asof_date: str = "2025-01-02",
    timestamp_ns: int = 10,
) -> FactorScoreData:
    return FactorScoreData(
        canonical_id=canonical_id,
        security_id=security_id or f"isin:{canonical_id}",
        asof_date=asof_date,
        score=score,
        eligible=True,
        batch_id=batch_id,
        batch_size=batch_size,
        delivery_id="d" * 64,
        model_release_id="r" * 64,
        source_kind=source_kind,
        ts_event=timestamp_ns,
        ts_init=timestamp_ns,
    )


def test_actor_waits_for_complete_batch_and_deduplicates(tmp_path: Path) -> None:
    actor = _actor(tmp_path)
    aapl = _row("AAPL.US", score=2.0)
    msft = _row("MSFT.US", score=1.0)
    actor._ingest_factor(aapl, publish=True)
    actor._ingest_factor(aapl, publish=True)
    assert actor.published == []
    actor._ingest_factor(msft, publish=True)
    assert actor.published == [(aapl, msft)]
    actor._ingest_factor(aapl, publish=True)
    actor._ingest_factor(msft, publish=True)
    assert actor.published == [(aapl, msft)]


def test_historical_and_stream_inputs_share_ingest_path(tmp_path: Path) -> None:
    actor = _actor(tmp_path)
    actor.on_historical_data(_row("AAPL.US"))
    actor.on_historical_data(_row("MSFT.US"))
    assert actor.published == []
    actor._catalog_request_completed(UUID4())
    assert len(actor.published) == 1

    stream = _actor(tmp_path / "stream")
    stream.on_data(_row("AAPL.US"))
    stream.on_data(_row("MSFT.US"))
    assert len(stream.published) == 1
    assert [value.to_dict() for value in actor.published[0]] == [
        value.to_dict() for value in stream.published[0]
    ]


def test_actor_ignores_evaluation_data_unless_explicitly_enabled(tmp_path: Path) -> None:
    blocked = _actor(tmp_path)
    blocked.on_data(_row("AAPL.US", batch_size=1, source_kind="evaluation_predictions"))
    assert blocked.published == []

    enabled = _actor(tmp_path / "enabled", allow_evaluation=True)
    enabled.on_data(_row("AAPL.US", batch_size=1, source_kind="evaluation_predictions"))
    assert len(enabled.published) == 1


def test_actor_completes_full_catalog_batch_for_selected_backtest_subset(
    tmp_path: Path,
) -> None:
    """CLI 选择子集时仍须等齐导入时定义的完整批次。"""
    actor = _actor(tmp_path, instrument_ids=("AAPL.US",))
    actor.on_data(_row("AAPL.US"))
    actor.on_data(_row("MSFT.US"))
    assert len(actor.published) == 1


def test_actor_rejects_conflicting_or_changing_batch(tmp_path: Path) -> None:
    actor = _actor(tmp_path)
    actor._ingest_factor(_row("AAPL.US"), publish=True)
    with pytest.raises(ValueError, match="conflicting"):
        actor._ingest_factor(_row("AAPL.US", score=2.0), publish=True)
    with pytest.raises(ValueError, match="size changed"):
        actor._ingest_factor(_row("MSFT.US", batch_size=3), publish=True)

    completed = _actor(tmp_path / "completed")
    completed._ingest_factor(_row("AAPL.US"), publish=True)
    completed._ingest_factor(_row("MSFT.US"), publish=True)
    with pytest.raises(ValueError, match="completed batch"):
        completed._ingest_factor(_row("AAPL.US", score=2.0), publish=True)


def test_actor_publishes_audited_signal_and_deduplicates_workflow(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path}/publish.db"
    actor = _PublishingActor(
        PatchTSTFactorActorConfig(
            instrument_ids=("AAPL.US", "MSFT.US"),
            top_n=1,
            target_gross_exposure=0.25,
            signal_expiry_hours=24,
            database_url=database_url,
            stream_data=False,
        )
    )
    actor.test_clock.set_time(20)
    actor._repository = TradingRepository(database_url)
    actor._repository.create_schema()
    batch = (_row("AAPL.US", score=2.0), _row("MSFT.US", score=1.0))

    actor._publish_signal(batch)
    actor._publish_signal(batch)

    assert len(actor.events) == 2
    event = actor.events[1]
    assert event.target_weights == (("AAPL.US", 0.25),)
    assert event.rebalance_key.endswith(":2025-01-02")
    assert "top_n=1" in event.reason
    with create_engine(database_url).connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SignalRecord)) == 1
    actor.on_stop()
    assert actor._repository is None


@pytest.mark.parametrize("source_kind", ["signal_inference", "evaluation_predictions"])
def test_actor_keeps_canonical_targets_across_identity_change(
    tmp_path: Path, source_kind: str
) -> None:
    """身份切换不改变 Actor 的标的、完整批次和逐日审计语义。"""
    database_url = f"sqlite:///{tmp_path}/identity-change.db"
    actor = _PublishingActor(
        PatchTSTFactorActorConfig(
            instrument_ids=("AAPL.US",),
            top_n=1,
            target_gross_exposure=0.25,
            signal_expiry_hours=24,
            database_url=database_url,
            stream_data=False,
            allow_evaluation_predictions=source_kind == "evaluation_predictions",
        )
    )
    actor._repository = TradingRepository(database_url)
    actor._repository.create_schema()
    for index, (session, identity) in enumerate(
        (("2025-01-02", "example:old"), ("2025-01-03", "example:new")), start=1
    ):
        timestamp_ns = index * 86_400_000_000_000
        actor.test_clock.set_time(timestamp_ns + 1)
        row = _row(
            "AAPL.US",
            security_id=identity,
            asof_date=session,
            batch_id=f"delivery:{session}",
            batch_size=1,
            source_kind=source_kind,
            timestamp_ns=timestamp_ns,
        )
        actor.on_data(row)
        actor.on_data(row)
    assert len(actor.events) == 2
    assert all(event.target_weights == (("AAPL.US", 0.25),) for event in actor.events)
    assert [event.rebalance_key.rsplit(":", 1)[1] for event in actor.events] == [
        "2025-01-02",
        "2025-01-03",
    ]
    assert actor.events[0].ts_event < actor.events[1].ts_event
    with create_engine(database_url).connect() as connection:
        assert connection.scalar(select(func.count()).select_from(SignalRecord)) == 2
    actor.on_stop()


def test_actor_reset_and_empty_catalog_callback_are_safe(tmp_path: Path) -> None:
    actor = _actor(tmp_path)
    actor._ingest_factor(_row("AAPL.US"), publish=False)
    actor._catalog_request_completed(UUID4())
    actor.on_reset()
    assert actor._pending_batches == {}
    assert actor._batch_sizes == {}
    assert actor._completed_batches == {}
