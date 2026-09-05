"""基本面纯计算的财报期间、适用性和严格恢复测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tests.market_radar.test_eodhd_fundamentals import CAPTURE, observation
from trading_assistant.market_radar.fundamentals import (
    FundamentalMetric,
    FundamentalObservation,
    FundamentalSnapshot,
    calculate_fundamental_snapshot,
)

CALCULATED = datetime(2026, 9, 5, 1, tzinfo=UTC)


def changed(**fields: object) -> FundamentalObservation:
    """所有输入改动都经过生产模型校验, 不使用跳过校验的 model_copy。"""
    return FundamentalObservation.model_validate({**observation().model_dump(), **fields})


def snapshot(item: FundamentalObservation | None = None) -> FundamentalSnapshot:
    return calculate_fundamental_snapshot(
        as_of_date=CAPTURE,
        calculated_at_utc=CALCULATED,
        observations=(item or observation(),),
    )


def test_exact_ratios_and_vendor_background_roundtrip() -> None:
    result = snapshot()
    metrics = {metric.name: metric for metric in result.items[0].metrics}
    assert result.validity == "complete"
    assert metrics["fcf_margin"].value == pytest.approx(100 / 460)
    assert metrics["net_debt_to_ebitda"].value == pytest.approx(55 / 110)
    assert metrics["fcf_yield"].value == pytest.approx(100 / 1000)
    assert metrics["forward_pe"].value == 20
    assert metrics["forward_pe"].period_end is None
    assert metrics["return_on_equity_ttm"].period_end == date(2026, 6, 30)
    assert FundamentalSnapshot.from_payload(result.to_payload()) == result
    assert "roic" not in result.model_dump_json()
    assert "fy1_earnings_yield" not in result.model_dump_json()


def test_negative_fcf_and_net_cash_are_valid() -> None:
    data = observation().model_dump()
    data["cash_flow_quarters"][0]["free_cash_flow"] = -200.0
    data["balance_sheet"]["net_debt"] = -55.0
    data["return_on_equity_ttm"] = -0.1
    result = snapshot(FundamentalObservation.model_validate(data))
    assert result.validity == "complete"
    values = {metric.name: metric.value for metric in result.items[0].metrics}
    assert values["fcf_margin"] == pytest.approx(-110 / 460)
    assert values["fcf_yield"] == pytest.approx(-0.11)
    assert values["net_debt_to_ebitda"] == pytest.approx(-0.5)
    assert values["return_on_equity_ttm"] == -0.1


@pytest.mark.parametrize("days", [91, 98])
def test_52_and_53_week_fiscal_calendars_do_not_require_calendar_quarter_ends(days: int) -> None:
    data = observation().model_dump()
    ends = [date(2026, 7, 25), date(2026, 7, 25) - timedelta(days=days)]
    ends.extend([ends[-1] - timedelta(days=91), ends[-1] - timedelta(days=182)])
    for section in ("income_quarters", "cash_flow_quarters"):
        for row, end in zip(data[section], ends, strict=True):
            row["period_end"] = end
    data["balance_sheet"]["period_end"] = ends[0]
    assert snapshot(FundamentalObservation.model_validate(data)).validity == "complete"


@pytest.mark.parametrize(
    ("section", "field", "value", "metric", "reason"),
    [
        ("cash_flow_quarters", "free_cash_flow", None, "fcf_margin", "missing_field"),
        ("cash_flow_quarters", "currency", None, "fcf_yield", "missing_currency"),
        ("cash_flow_quarters", "currency", "EUR", "fcf_yield", "currency_mismatch"),
        ("income_quarters", "currency", "EUR", "fcf_margin", "currency_mismatch"),
        ("income_quarters", "revenue", -1000.0, "fcf_margin", "invalid_denominator"),
        ("income_quarters", "ebitda", -1000.0, "net_debt_to_ebitda", "invalid_denominator"),
        ("income_quarters", "period_end", date(2026, 7, 1), "fcf_margin", "period_mismatch"),
        ("income_quarters", "period_end", date(2026, 8, 1), "fcf_margin", "non_contiguous_periods"),
    ],
)
def test_per_metric_reasons_preserve_legal_input_gaps(
    section: str,
    field: str,
    value: object,
    metric: str,
    reason: str,
) -> None:
    data = observation().model_dump()
    data[section][0][field] = value
    result = snapshot(FundamentalObservation.model_validate(data))
    item = next(item for item in result.items[0].metrics if item.name == metric)
    assert result.validity == "partial"
    assert item.value is None
    assert item.reason == reason


@pytest.mark.parametrize("count", [0, 1, 3])
def test_insufficient_quarters_never_form_partial_ttm(count: int) -> None:
    obs = observation()
    result = snapshot(
        changed(
            income_quarters=obs.income_quarters[:count],
            cash_flow_quarters=obs.cash_flow_quarters[:count],
        )
    )
    assert all(metric.reason == "insufficient_history" for metric in result.items[0].metrics[:3])


@pytest.mark.parametrize("gap", [75, 105])
def test_four_ends_must_also_span_a_full_ttm_window(gap: int) -> None:
    data = observation().model_dump()
    for index, row in enumerate(data["cash_flow_quarters"]):
        row["period_end"] = date(2026, 6, 30) - timedelta(days=index * gap)
    result = snapshot(FundamentalObservation.model_validate(data))
    assert result.items[0].metrics[2].reason == "non_contiguous_periods"


def test_latest_balance_alignment_and_missing_balance() -> None:
    assert snapshot(changed(balance_sheet=None)).items[0].metrics[1].reason == "missing_field"
    data = observation().model_dump()
    data["balance_sheet"]["period_end"] = date(2026, 3, 31)
    assert (
        snapshot(FundamentalObservation.model_validate(data)).items[0].metrics[1].reason
        == "period_mismatch"
    )


@pytest.mark.parametrize(
    ("field", "value", "metric", "reason"),
    [
        ("market_capitalization", 0.0, "fcf_yield", "invalid_denominator"),
        ("market_capitalization", None, "fcf_yield", "missing_field"),
        ("forward_pe", 0.0, "forward_pe", "non_positive_multiple"),
        ("forward_pe", -2.0, "forward_pe", "non_positive_multiple"),
        ("price_to_book", None, "price_to_book", "missing_field"),
        ("enterprise_value_to_ebitda", -3.0, "enterprise_value_to_ebitda", "non_positive_multiple"),
    ],
)
def test_invalid_denominators_and_raw_multiples(
    field: str,
    value: object,
    metric: str,
    reason: str,
) -> None:
    result = snapshot(changed(**{field: value}))
    assert next(item for item in result.items[0].metrics if item.name == metric).reason == reason


@pytest.mark.parametrize("overflow_sum", [True, False])
def test_finite_inputs_cannot_publish_infinite_derived_values(overflow_sum: bool) -> None:
    data = observation().model_dump()
    if overflow_sum:
        for row in data["cash_flow_quarters"]:
            row["free_cash_flow"] = 1e308
    else:
        data["market_capitalization"] = 1e-320
    result = snapshot(FundamentalObservation.model_validate(data))
    assert result.items[0].metrics[2].reason == "non_finite_result"


@pytest.mark.parametrize("kind", ["financial", "reit", "unknown"])
def test_classification_controls_applicability(kind: str) -> None:
    result = snapshot(changed(kind=kind))
    metrics = result.items[0].metrics
    if kind == "financial":
        assert all(metric.reason == "not_applicable" for metric in metrics[:5])
        assert all(metric.value is not None for metric in metrics[5:])
        assert result.validity == "complete"
    else:
        expected = "not_applicable" if kind == "reit" else "unknown_classification"
        assert all(metric.reason == expected for metric in metrics)
        assert result.validity == "unavailable"


@pytest.mark.parametrize(
    "fields",
    [
        {"source_updated_date": CAPTURE + timedelta(days=1)},
        {"kind": "financial", "industry": None},
        {"income_quarters": observation().income_quarters[::-1]},
        {"income_quarters": (*observation().income_quarters, observation().income_quarters[-1])},
        {"forward_pe": float("nan")},
        {"market_capitalization": True},
        {"instrument_id": "not canonical"},
    ],
)
def test_invalid_normalized_inputs_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        changed(**fields)


def test_future_financial_period_is_rejected() -> None:
    data = observation().model_dump()
    data["income_quarters"][0]["period_end"] = CAPTURE + timedelta(days=1)
    with pytest.raises(ValueError, match="future"):
        FundamentalObservation.model_validate(data)


@pytest.mark.parametrize("case", ["empty", "duplicate", "source", "capture", "naive", "cross_day"])
def test_batch_dates_universe_and_sources_are_checked(case: str) -> None:
    observations: tuple[FundamentalObservation, ...] = (observation(),)
    calculated = CALCULATED
    if case == "empty":
        observations = ()
    elif case == "duplicate":
        observations *= 2
    elif case == "source":
        observations += (changed(instrument_id="MSFT.US", source="other"),)
    elif case == "capture":
        observations = (changed(captured_on=CAPTURE + timedelta(days=1)),)
    elif case == "naive":
        calculated = CALCULATED.replace(tzinfo=None)
    else:
        calculated += timedelta(days=1)
    with pytest.raises(ValueError, match=r"fundamental|validation error"):
        calculate_fundamental_snapshot(
            as_of_date=CAPTURE, calculated_at_utc=calculated, observations=observations
        )


@pytest.mark.parametrize(
    "case",
    [
        "validity",
        "extra",
        "future",
        "missing_metric",
        "duplicate_metric",
        "value_reason",
        "kind",
        "nan",
        "future_update",
    ],
)
def test_persisted_payload_tampering_is_rejected(case: str) -> None:
    payload = snapshot().to_payload()
    item = payload["items"][0]
    metric = item["metrics"][0]
    if case == "validity":
        payload["validity"] = "partial"
    elif case == "extra":
        payload["schema_version"] = 1
    elif case == "future":
        metric["period_end"] = "2027-01-01"
    elif case == "future_update":
        item["source_updated_date"] = "2027-01-01"
    elif case == "missing_metric":
        item["metrics"].pop()
    elif case == "duplicate_metric":
        item["metrics"][1] = metric
    elif case == "value_reason":
        metric["reason"] = "missing_field"
    elif case == "kind":
        item["kind"] = "financial"
    else:
        metric["value"] = float("nan")
    with pytest.raises(ValueError, match=r"validation error|Out of range float"):
        FundamentalSnapshot.from_payload(payload)


def test_inapplicable_reason_cannot_hide_a_valid_operating_metric() -> None:
    result = snapshot().model_dump()
    metrics = result["items"][0]["metrics"]
    result["items"][0]["metrics"] = (
        FundamentalMetric(
            name="fcf_margin",
            value=None,
            reason="not_applicable",
            period_end=None,
        ),
        *metrics[1:],
    )
    with pytest.raises(ValueError, match="applicability"):
        FundamentalSnapshot.model_validate(result)
