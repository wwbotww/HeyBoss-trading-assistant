"""经济事件的中立规范批次; 来源时钟不代表已确认的 UTC 发布时间。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _EconomicModel(BaseModel):
    """拒绝隐式转换、多余字段、非有限值及绕过验证的嵌套实例。"""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class EconomicEvent(_EconomicModel):
    """一个美国事件; 数值保持供应商语义, 不推导单位或 surprise。"""

    country: Literal["US"]
    event_type: Annotated[str, Field(min_length=1, pattern=r"^\S(?:.*\S)?$")]
    event_date: date
    source_time: (
        Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")] | None
    )
    comparison: Literal["mom", "qoq", "yoy"] | None
    period: Annotated[str, Field(min_length=1, pattern=r"^\S(?:.*\S)?$")] | None
    actual: float | None
    estimate: float | None
    previous: float | None
    change: float | None
    change_percentage: float | None

    @property
    def identity(self) -> tuple[str, date, str | None, str, str | None, str | None]:
        """批次内业务键, 不承诺改期前后具有稳定身份。"""
        return (
            self.country,
            self.event_date,
            self.source_time,
            self.event_type,
            self.comparison,
            self.period,
        )

    @property
    def sort_key(self) -> tuple[date, str, str, str, str]:
        """确定性排列, 不以无时区时钟推断盘中发布顺序。"""
        return (
            self.event_date,
            self.event_type,
            self.comparison or "",
            self.period or "",
            self.source_time or "",
        )


class EconomicEventBatch(_EconomicModel):
    """一页规范结果或两页合并批次; 不保留供应商原始响应。"""

    events: tuple[EconomicEvent, ...]
    request_count: Annotated[int, Field(ge=1, le=2)]
    raw_record_count: Annotated[int, Field(ge=0, le=1999)]
    duplicate_count: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        """核对页数、行数、唯一键与排列; 满首页面留待适配器续取。"""
        if self.raw_record_count != len(self.events) + self.duplicate_count:
            raise ValueError("economic event batch counts are inconsistent")
        if (self.request_count == 1 and self.raw_record_count > 1000) or (
            self.request_count == 2 and self.raw_record_count < 1000
        ):
            raise ValueError("economic event page counts are inconsistent")
        if not self.events and self.duplicate_count:
            raise ValueError("economic event duplicates require at least one event")
        identities = tuple(item.identity for item in self.events)
        if len(identities) != len(set(identities)):
            raise ValueError("economic event batch contains duplicate identities")
        if self.events != tuple(sorted(self.events, key=lambda item: item.sort_key)):
            raise ValueError("economic event batch must be sorted")
        return self


class EconomicEventSnapshot(_EconomicModel):
    """当前 UTC 采集日的完整请求结果, 不具有历史 PIT 语义。"""

    as_of_date: date
    captured_at_utc: datetime
    source: Literal["eodhd_economic_events"]
    country: Literal["US"]
    window_start: date
    window_end: date
    batch: EconomicEventBatch

    @field_validator("captured_at_utc")
    @classmethod
    def normalize_capture_time(cls, value: datetime) -> datetime:
        """采集时钟必须带时区, 统一规范为 UTC。"""
        if value.utcoffset() is None:
            raise ValueError("economic event capture time must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """只发布固定 31 日闭区间且已到达分页末尾的批次。"""
        if self.as_of_date != self.captured_at_utc.date():
            raise ValueError("economic event snapshot must use its UTC capture date")
        if self.window_start != self.as_of_date or (self.window_end - self.window_start).days != 30:
            raise ValueError("economic event snapshot requires its 31-date capture window")
        if self.batch.request_count == 1 and self.batch.raw_record_count == 1000:
            raise ValueError("economic event snapshot has an unterminated first page")
        if any(
            not self.window_start <= item.event_date <= self.window_end
            for item in self.batch.events
        ):
            raise ValueError("economic event snapshot contains an out-of-range event")
        return self

    def to_payload(self) -> dict[str, Any]:
        """输出无格式版本的规范 JSON。"""
        return self.model_dump(mode="json")

    @classmethod
    def from_payload(cls, payload: object) -> EconomicEventSnapshot:
        """完整重新校验持久化数据, 不对坏值或未知字段做兼容。"""
        return cls.model_validate_json(json.dumps(payload, allow_nan=False))
