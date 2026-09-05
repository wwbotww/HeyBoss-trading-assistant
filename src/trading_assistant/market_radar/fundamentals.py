"""当前基本面的规范输入、可解释比率与严格快照模型。"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from datetime import UTC, date, datetime
from itertools import pairwise
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Text = Annotated[str, Field(min_length=1, pattern=r"^\S(?:.*\S)?$")]
CanonicalId = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9.-]*\.[A-Z0-9]+$")]
FundamentalKind = Literal["operating", "financial", "reit", "unknown"]
FundamentalValidity = Literal["complete", "partial", "unavailable"]
FundamentalMetricName = Literal[
    "fcf_margin",
    "net_debt_to_ebitda",
    "fcf_yield",
    "forward_pe",
    "enterprise_value_to_ebitda",
    "return_on_equity_ttm",
    "price_to_book",
]
FundamentalReason = Literal[
    "missing_field",
    "insufficient_history",
    "non_contiguous_periods",
    "period_mismatch",
    "missing_currency",
    "currency_mismatch",
    "invalid_denominator",
    "non_positive_multiple",
    "non_finite_result",
    "not_applicable",
    "unknown_classification",
]
METRIC_NAMES: tuple[FundamentalMetricName, ...] = (
    "fcf_margin",
    "net_debt_to_ebitda",
    "fcf_yield",
    "forward_pe",
    "enterprise_value_to_ebitda",
    "return_on_equity_ttm",
    "price_to_book",
)


class _FundamentalModel(BaseModel):
    """规范数据拒绝隐式类型转换、多余字段与非有限数值。"""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


class FinancialPeriod(_FundamentalModel):
    """三个报表共用的财政期与来源时间。"""

    period_end: date
    filing_date: date | None
    currency: Text | None

    @model_validator(mode="after")
    def validate_filing_date(self) -> Self:
        """报告提交日不得早于对应财政期末。"""
        if self.filing_date is not None and self.filing_date < self.period_end:
            raise ValueError("financial filing date precedes its period")
        return self


class IncomeQuarter(FinancialPeriod):
    """计算同期 TTM 分母所需的单季收入与 EBITDA。"""

    revenue: float | None
    ebitda: float | None


class CashFlowQuarter(FinancialPeriod):
    """供应商单季自由现金流, 负值保留。"""

    free_cash_flow: float | None


class BalanceSheetQuarter(FinancialPeriod):
    """最新一季净债务; 负值代表净现金。"""

    net_debt: float | None


class FundamentalObservation(_FundamentalModel):
    """一次采集保留的最小可复算输入, 不包含供应商原始响应。"""

    instrument_id: CanonicalId
    data_symbol: CanonicalId
    source: Text
    captured_on: date
    source_updated_date: date | None
    most_recent_quarter: date | None
    listing_currency: Text
    provider_sector: Text | None
    sector_id: Text | None
    industry: Text | None
    kind: FundamentalKind
    income_quarters: tuple[IncomeQuarter, ...]
    cash_flow_quarters: tuple[CashFlowQuarter, ...]
    balance_sheet: BalanceSheetQuarter | None
    market_capitalization: float | None
    forward_pe: float | None
    enterprise_value_to_ebitda: float | None
    return_on_equity_ttm: float | None
    price_to_book: float | None

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        """校验采集日期、排序和最小输入规模, 缺季留给指标解释。"""
        if self.source_updated_date is not None and self.source_updated_date > self.captured_on:
            raise ValueError("fundamental source update is in the future")
        if self.most_recent_quarter is not None and self.most_recent_quarter > self.captured_on:
            raise ValueError("fundamental highlight period is in the future")
        for periods in (self.income_quarters, self.cash_flow_quarters):
            ends = tuple(item.period_end for item in periods)
            if len(ends) > 4 or ends != tuple(sorted(set(ends), reverse=True)):
                raise ValueError("fundamental quarters must be unique, descending and at most four")
        all_periods: tuple[FinancialPeriod, ...] = (
            *self.income_quarters,
            *self.cash_flow_quarters,
            *((self.balance_sheet,) if self.balance_sheet is not None else ()),
        )
        if any(
            item.period_end > self.captured_on
            or (item.filing_date is not None and item.filing_date > self.captured_on)
            for item in all_periods
        ):
            raise ValueError("fundamental statement date is in the future")
        if self.kind != "unknown" and (self.sector_id is None or self.industry is None):
            raise ValueError("fundamental applicability requires sector and industry")
        return self


class FundamentalMetric(_FundamentalModel):
    """一个比率及实际报告期; 无数值时必须解释原因。"""

    name: FundamentalMetricName
    value: float | None
    reason: FundamentalReason | None
    period_end: date | None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        """禁止不可用指标同时携带数值, 或无原因地返回 null。"""
        if (self.value is None) != (self.reason is not None):
            raise ValueError("fundamental metric value and reason are inconsistent")
        return self


class StockFundamentals(_FundamentalModel):
    """一只观察股的财务指标与独立供应商估值背景。"""

    instrument_id: CanonicalId
    source_updated_date: date | None
    listing_currency: Text
    provider_sector: Text | None
    sector_id: Text | None
    industry: Text | None
    kind: FundamentalKind
    metrics: tuple[FundamentalMetric, ...]

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        """快照只接受当前已实现的七个指标。"""
        if tuple(metric.name for metric in self.metrics) != METRIC_NAMES:
            raise ValueError("fundamental metrics are incomplete or duplicated")
        for metric in self.metrics:
            excluded = self.kind == "reit" or (
                self.kind == "financial"
                and metric.name not in {"return_on_equity_ttm", "price_to_book"}
            )
            if (excluded and metric.reason != "not_applicable") or (
                self.kind == "unknown" and metric.reason != "unknown_classification"
            ):
                raise ValueError("fundamental metric applicability is inconsistent")
            if (
                not excluded
                and self.kind != "unknown"
                and metric.reason
                in {
                    "not_applicable",
                    "unknown_classification",
                }
            ):
                raise ValueError("fundamental metric applicability is inconsistent")
        return self


def _validity(items: Sequence[StockFundamentals]) -> FundamentalValidity:
    relevant = [
        metric for item in items for metric in item.metrics if metric.reason != "not_applicable"
    ]
    available = sum(metric.value is not None for metric in relevant)
    if available == 0:
        return "unavailable"
    return "complete" if available == len(relevant) else "partial"


class FundamentalSnapshot(_FundamentalModel):
    """一次采集日的派生结果, 与运行 COMPLETE 状态分别表达。"""

    as_of_date: date
    calculated_at_utc: datetime
    source: Text
    validity: FundamentalValidity
    items: tuple[StockFundamentals, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """拒绝未来时间、重复标的或自相矛盾的完整状态。"""
        if self.calculated_at_utc.utcoffset() is None:
            raise ValueError("fundamental calculation time must be timezone-aware")
        if self.as_of_date != self.calculated_at_utc.astimezone(UTC).date():
            raise ValueError("fundamental snapshot must use its UTC capture date")
        ids = tuple(item.instrument_id for item in self.items)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("fundamental snapshot instruments must be non-empty and unique")
        if self.validity != _validity(self.items):
            raise ValueError("fundamental snapshot validity does not match its metrics")
        for item in self.items:
            dates = (item.source_updated_date, *(metric.period_end for metric in item.metrics))
            if any(value is not None and value > self.as_of_date for value in dates):
                raise ValueError("fundamental snapshot contains a future source date")
        return self

    def to_payload(self) -> dict[str, Any]:
        """输出无版本号的规范 JSON 数据。"""
        return self.model_dump(mode="json")

    @classmethod
    def from_payload(cls, payload: object) -> FundamentalSnapshot:
        """按 JSON 类型严格恢复持久化结果。"""
        return cls.model_validate_json(json.dumps(payload, allow_nan=False))


def _ttm_reason(periods: Sequence[FinancialPeriod]) -> FundamentalReason | None:
    if len(periods) < 4:
        return "insufficient_history"
    ends = [item.period_end for item in periods]
    if any(not 70 <= (a - b).days <= 110 for a, b in pairwise(ends)):
        return "non_contiguous_periods"
    if not 250 <= (ends[0] - ends[-1]).days <= 300:
        return "non_contiguous_periods"
    return None


def _currency_reason(
    periods: Sequence[FinancialPeriod],
    currency: str,
) -> FundamentalReason | None:
    if any(item.currency is None for item in periods):
        return "missing_currency"
    if any(item.currency != currency for item in periods):
        return "currency_mismatch"
    return None


def _sum(values: Sequence[float | None]) -> float | None:
    return (
        None
        if any(value is None for value in values)
        else sum(value for value in values if value is not None)
    )


def _ratio(
    name: FundamentalMetricName,
    numerator: float | None,
    denominator: float | None,
    *,
    reason: FundamentalReason | None,
    period_end: date | None,
) -> FundamentalMetric:
    if reason is None:
        if numerator is None or denominator is None:
            reason = "missing_field"
        elif not math.isfinite(numerator) or not math.isfinite(denominator):
            reason = "non_finite_result"
        elif denominator <= 0:
            reason = "invalid_denominator"
    value = None
    if reason is None and numerator is not None and denominator is not None:
        value = numerator / denominator
        if not math.isfinite(value):
            value, reason = None, "non_finite_result"
    return FundamentalMetric(name=name, value=value, reason=reason, period_end=period_end)


def _stock(observation: FundamentalObservation) -> StockFundamentals:
    income, cash = observation.income_quarters, observation.cash_flow_quarters
    balance = observation.balance_sheet
    income_end = income[0].period_end if income else None
    cash_end = cash[0].period_end if cash else None
    income_reason = _ttm_reason(income) or _currency_reason(income, observation.listing_currency)
    cash_reason = _ttm_reason(cash) or _currency_reason(cash, observation.listing_currency)
    margin_reason = income_reason or cash_reason
    if margin_reason is None and tuple(row.period_end for row in income) != tuple(
        row.period_end for row in cash
    ):
        margin_reason = "period_mismatch"
    debt_reason = income_reason
    if debt_reason is None:
        if balance is None:
            debt_reason = "missing_field"
        elif balance.period_end != income_end:
            debt_reason = "period_mismatch"
        else:
            debt_reason = _currency_reason((balance,), observation.listing_currency)
    fcf = _sum([row.free_cash_flow for row in cash])
    metrics = [
        _ratio(
            "fcf_margin",
            fcf,
            _sum([row.revenue for row in income]),
            reason=margin_reason,
            period_end=cash_end,
        ),
        _ratio(
            "net_debt_to_ebitda",
            None if balance is None else balance.net_debt,
            _sum([row.ebitda for row in income]),
            reason=debt_reason,
            period_end=income_end,
        ),
        _ratio(
            "fcf_yield",
            fcf,
            observation.market_capitalization,
            reason=cash_reason,
            period_end=cash_end,
        ),
    ]
    vendor_values: tuple[tuple[FundamentalMetricName, float | None], ...] = (
        ("forward_pe", observation.forward_pe),
        ("enterprise_value_to_ebitda", observation.enterprise_value_to_ebitda),
        ("return_on_equity_ttm", observation.return_on_equity_ttm),
        ("price_to_book", observation.price_to_book),
    )
    for name, value in vendor_values:
        reason: FundamentalReason | None = None
        if value is None:
            reason = "missing_field"
        elif name != "return_on_equity_ttm" and value <= 0:
            reason = "non_positive_multiple"
        metrics.append(
            FundamentalMetric(
                name=name,
                value=value if reason is None else None,
                reason=reason,
                period_end=(
                    observation.most_recent_quarter if name == "return_on_equity_ttm" else None
                ),
            )
        )
    for index, metric in enumerate(metrics):
        if observation.kind == "unknown":
            metrics[index] = FundamentalMetric(
                name=metric.name,
                value=None,
                reason="unknown_classification",
                period_end=None,
            )
        elif observation.kind == "reit" or (
            observation.kind == "financial"
            and metric.name
            not in {
                "return_on_equity_ttm",
                "price_to_book",
            }
        ):
            metrics[index] = FundamentalMetric(
                name=metric.name,
                value=None,
                reason="not_applicable",
                period_end=None,
            )
    return StockFundamentals(
        instrument_id=observation.instrument_id,
        source_updated_date=observation.source_updated_date,
        listing_currency=observation.listing_currency,
        provider_sector=observation.provider_sector,
        sector_id=observation.sector_id,
        industry=observation.industry,
        kind=observation.kind,
        metrics=tuple(metrics),
    )


def calculate_fundamental_snapshot(
    *,
    as_of_date: date,
    calculated_at_utc: datetime,
    observations: Sequence[FundamentalObservation],
) -> FundamentalSnapshot:
    """只基于规范观测计算当前单项比率, 不计算 ROIC 或同行分数。"""
    sources = {item.source for item in observations}
    if len(sources) != 1 or any(item.captured_on != as_of_date for item in observations):
        raise ValueError("fundamental inputs must share one source and capture date")
    items = tuple(_stock(item) for item in observations)
    return FundamentalSnapshot(
        as_of_date=as_of_date,
        calculated_at_utc=calculated_at_utc,
        source=next(iter(sources)),
        validity=_validity(items),
        items=items,
    )
