"""经济事件中立模型测试; 所有事件均为合成数据。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from trading_assistant.market_radar.economic_events import (
    EconomicEvent,
    EconomicEventBatch,
    EconomicEventSnapshot,
)

ECONOMIC_AT = datetime(2026, 9, 5, 1, tzinfo=UTC)


def economic_event(**changes: object) -> EconomicEvent:
    """共享的无真实市场信息的模型样本。"""
    data: dict[str, object] = {
        "country": "US",
        "event_type": "Synthetic Index",
        "event_date": ECONOMIC_AT.date(),
        "source_time": "08:30:00",
        "comparison": "mom",
        "period": "Aug",
        "actual": 0.0,
        "estimate": None,
        "previous": -1.0,
        "change": 0.0,
        "change_percentage": None,
    }
    data.update(changes)
    return EconomicEvent.model_validate(data)


def economic_snapshot(**changes: object) -> EconomicEventSnapshot:
    """供模型、仓储和 CLI 测试复用的同一份严格快照。"""
    data: dict[str, object] = {
        "as_of_date": ECONOMIC_AT.date(),
        "captured_at_utc": ECONOMIC_AT,
        "source": "eodhd_economic_events",
        "country": "US",
        "window_start": ECONOMIC_AT.date(),
        "window_end": ECONOMIC_AT.date() + timedelta(days=30),
        "batch": EconomicEventBatch(
            events=(economic_event(),),
            request_count=1,
            raw_record_count=1,
            duplicate_count=0,
        ),
    }
    data.update(changes)
    return EconomicEventSnapshot.model_validate(data)


def test_snapshot_roundtrip_is_strict_frozen_and_preserves_unknowns() -> None:
    original = economic_snapshot()
    assert EconomicEventSnapshot.from_payload(original.to_payload()) == original
    assert original.batch.events[0].actual == 0
    assert original.batch.events[0].previous == -1
    assert original.batch.events[0].estimate is None
    assert original.batch.events[0].source_time == "08:30:00"
    assert "event_time_utc" not in original.to_payload()
    assert "available_at_utc" not in original.to_payload()
    assert "importance" not in original.to_payload()
    with pytest.raises(ValidationError, match="frozen"):
        original.country = "US"
    with pytest.raises(ValidationError, match="frozen"):
        original.batch.events[0].actual = 99.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("country", "CA"),
        ("event_type", ""),
        ("event_type", " name "),
        ("period", ""),
        ("period", 1),
        ("comparison", "weekly"),
        ("source_time", "24:00:00"),
        ("source_time", "08:60:00"),
        ("source_time", "08:30:60"),
        ("source_time", "8:30:00"),
        ("source_time", "08:30:00Z"),
        ("source_time", "08:30:00\n"),
        ("event_date", "2026-09-05"),
        ("event_date", ECONOMIC_AT),
        ("actual", "1"),
        ("actual", True),
        ("estimate", float("nan")),
        ("previous", float("inf")),
        ("change", float("-inf")),
        ("schema_version", 1),
    ],
)
def test_event_rejects_invalid_fields(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        economic_event(**{field: value})


def test_identity_keeps_time_comparison_and_period_distinct() -> None:
    events = (
        economic_event(),
        economic_event(comparison="yoy"),
        economic_event(period="Jul"),
        economic_event(source_time=None),
        economic_event(event_date=ECONOMIC_AT.date() + timedelta(days=1)),
    )
    assert len({item.identity for item in events}) == 5
    assert economic_event(actual=9.0).identity == economic_event().identity


@pytest.mark.parametrize(
    "changes",
    [
        {"request_count": 0},
        {"request_count": 3},
        {"request_count": True},
        {"raw_record_count": -1},
        {"duplicate_count": -1},
        {"raw_record_count": 2},
        {"request_count": 2},
        {"raw_record_count": 1001, "duplicate_count": 1000},
        {"request_count": 2, "raw_record_count": 2000, "duplicate_count": 1999},
        {"events": (economic_event(), economic_event()), "raw_record_count": 2},
        {"events": (), "duplicate_count": 1},
        {
            "events": (economic_event(event_type="Z"), economic_event(event_type="A")),
            "raw_record_count": 2,
        },
    ],
)
def test_batch_requires_consistent_counts_and_unique_sorted_events(
    changes: dict[str, object],
) -> None:
    data: dict[str, object] = {
        "events": (economic_event(),),
        "request_count": 1,
        "raw_record_count": 1,
        "duplicate_count": 0,
    }
    data.update(changes)
    with pytest.raises(ValidationError):
        EconomicEventBatch.model_validate(data)


def test_snapshot_accepts_empty_and_normalizes_only_capture_timezone() -> None:
    empty = EconomicEventBatch(events=(), request_count=1, raw_record_count=0, duplicate_count=0)
    assert economic_snapshot(batch=empty).batch.events == ()
    local = ECONOMIC_AT.astimezone(timezone(timedelta(hours=8)))
    assert economic_snapshot(captured_at_utc=local).captured_at_utc == ECONOMIC_AT
    assert economic_snapshot(captured_at_utc=local).captured_at_utc.tzinfo == UTC


@pytest.mark.parametrize(
    "changes",
    [
        {"captured_at_utc": ECONOMIC_AT.replace(tzinfo=None)},
        {"captured_at_utc": ECONOMIC_AT + timedelta(days=1)},
        {"window_start": date(2026, 9, 4)},
        {"window_end": date(2026, 10, 4)},
        {"source": "another_source"},
        {"country": "GB"},
        {"event_time_utc": ECONOMIC_AT},
        {
            "batch": EconomicEventBatch(
                events=(economic_event(),),
                request_count=1,
                raw_record_count=1000,
                duplicate_count=999,
            )
        },
        {
            "batch": EconomicEventBatch(
                events=(economic_event(event_date=date(2026, 10, 6)),),
                request_count=1,
                raw_record_count=1,
                duplicate_count=0,
            )
        },
    ],
)
def test_snapshot_rejects_wrong_capture_window_or_unterminated_page(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        economic_snapshot(**changes)


@pytest.mark.parametrize("case", ["extra", "number", "count", "date", "null", "nan"])
def test_persisted_payload_cannot_bypass_nested_validation(case: str) -> None:
    payload: dict[str, Any] = economic_snapshot().to_payload()
    if case == "extra":
        payload["batch"]["events"][0]["importance"] = "high"
    elif case == "number":
        payload["batch"]["events"][0]["actual"] = "9"
    elif case == "count":
        payload["batch"]["duplicate_count"] = 10
    elif case == "date":
        payload["captured_at_utc"] = "not-a-date"
    elif case == "null":
        payload["batch"] = None
    else:
        payload["batch"]["events"][0]["actual"] = float("nan")
    with pytest.raises(ValueError, match=r"validation error|Out of range float"):
        EconomicEventSnapshot.from_payload(payload)


def test_model_copy_does_not_bypass_publication_validation() -> None:
    invalid_event = economic_event().model_copy(update={"actual": "not numeric"})
    batch = economic_snapshot().batch.model_copy(update={"events": (invalid_event,)})
    invalid = economic_snapshot().model_copy(update={"batch": batch})
    with pytest.raises(ValidationError):
        EconomicEventSnapshot.model_validate(invalid)
