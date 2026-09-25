"""回测与 paper 共用的 PatchTST 因子决策 Actor。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import partial

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import ClientId

from trading_assistant.data.catalog import CatalogRequestOutcome
from trading_assistant.data.factor import (
    FACTOR_DATA_TYPE,
    FactorScoreData,
    expected_factor_date,
    factor_execution_window,
)
from trading_assistant.data.market_calendar import CALENDAR_VERSION
from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, FactorContext, TradeSignalEvent
from trading_assistant.signals.factor import calculate_factor_weights
from trading_assistant.storage.repository import TradingRepository


class PatchTSTFactorActorConfig(ActorConfig, frozen=True):
    """PatchTST 因子 Actor 配置。"""

    instrument_ids: tuple[str, ...]
    top_n: int
    target_gross_exposure: float
    signal_expiry_hours: int
    database_url: str
    strategy_name: str = "patchtst_e3"
    signal_topic: str = TRADE_SIGNAL_TOPIC
    signal_scope: str = "default"
    stream_data: bool = True
    bootstrap_from_catalog: bool = False
    data_client_id: str = "FACTOR"
    catalog_client_id: str = "CATALOG"
    catalog_lookback_days: int = 2_200
    publish_after_ns: int = 0
    allow_evaluation_predictions: bool = False
    model_release_id: str = ""
    catalog_path: str = ""
    max_unscorable_fraction: float = 0.0
    factor_check_interval_seconds: int = 1800


class PatchTSTFactorActor(Actor):  # type: ignore[misc]
    """聚合完整因子横截面并发布目标权重。"""

    def __init__(self, config: PatchTSTFactorActorConfig) -> None:
        super().__init__(config)
        self._settings = config
        self._expected_instruments = frozenset(config.instrument_ids)
        self._pending_batches: dict[str, dict[str, FactorScoreData]] = {}
        self._batch_sizes: dict[str, int] = {}
        self._completed_batches: dict[str, tuple[FactorScoreData, ...]] = {}
        self._request_started_ns: int | None = None
        self._request_generation = 0
        self._request_outcome: CatalogRequestOutcome | None = None
        self._request_rows: dict[str, set[str]] = {}
        self._scheduled_check_date: date | None = None
        self._repository: TradingRepository | None = None

    def on_start(self) -> None:
        """建立审计仓储并装配 NT CustomData 输入。"""
        if not self._settings.model_release_id:
            raise ValueError("factor strategy requires a fixed model_release_id")
        self._repository = TradingRepository(self._settings.database_url)
        self._repository.create_schema()
        self.clock.set_timer(
            "factor-check",
            interval=timedelta(seconds=self._settings.factor_check_interval_seconds),
            callback=self._check_factors,
        )
        if self._settings.stream_data:
            self.subscribe_data(
                FACTOR_DATA_TYPE,
                client_id=ClientId(self._settings.data_client_id),
            )
        self._check_factors(None)

    def on_data(self, data: object) -> None:
        """处理 BacktestNode 流式回放的 FactorScoreData。"""
        if isinstance(data, FactorScoreData):
            self._ingest_factor(data, publish=True)

    def on_historical_data(self, data: object) -> None:
        """处理 TradingNode 从同一 Catalog 请求的历史 FactorScoreData。"""
        if isinstance(data, FactorScoreData):
            if self._request_started_ns is not None:
                self._request_rows.setdefault(data.batch_id, set()).add(data.canonical_id)
            self._ingest_factor(data, publish=False)

    def _ingest_factor(self, data: FactorScoreData, *, publish: bool) -> None:
        if data.source_kind == "evaluation_predictions" and not (
            self._settings.allow_evaluation_predictions
        ):
            return
        completed = self._completed_batches.get(data.batch_id)
        if completed is not None:
            previous = next(
                (value for value in completed if value.canonical_id == data.canonical_id),
                None,
            )
            if previous is None or previous.to_dict() != data.to_dict():
                raise ValueError(f"conflicting completed batch row: {data.batch_id}")
            return
        expected_size = self._batch_sizes.setdefault(data.batch_id, data.batch_size)
        if data.batch_size != expected_size or expected_size < 1:
            raise ValueError(f"factor batch size changed: {data.batch_id}")
        pending = self._pending_batches.setdefault(data.batch_id, {})
        previous = pending.get(data.canonical_id)
        if previous is not None:
            if previous.to_dict() != data.to_dict():
                raise ValueError(f"conflicting factor row: {data.batch_id}/{data.canonical_id}")
            return
        pending[data.canonical_id] = data
        if len(pending) > expected_size:
            raise ValueError(f"factor batch exceeds declared size: {data.batch_id}")
        if len(pending) != expected_size:
            return
        completed_batch = tuple(sorted(pending.values(), key=lambda value: value.canonical_id))
        self._completed_batches[data.batch_id] = completed_batch
        del self._pending_batches[data.batch_id]
        timestamp_ns = max(value.ts_event for value in completed_batch)
        if publish and timestamp_ns >= self._settings.publish_after_ns:
            self._publish_signal(completed_batch)

    def _publish_signal(self, batch: tuple[FactorScoreData, ...]) -> None:
        representative = batch[0]
        now_ns = self.clock.timestamp_ns()
        now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=UTC)
        expected = expected_factor_date(now).isoformat()
        if representative.asof_date != expected:
            self._audit(expected, "SKIP", "unexpected_factor_date", now_ns)
            return
        if representative.model_release_id != self._settings.model_release_id:
            self._audit(expected, "SKIP", "unexpected_model_release", now_ns)
            return
        identity = {
            (
                row.asof_date,
                row.model_release_id,
                row.delivery_id,
                row.calendar_version,
                row.source_kind,
            )
            for row in batch
        }
        if len(identity) != 1 or representative.calendar_version != CALENDAR_VERSION:
            self._audit(expected, "SKIP", "invalid_factor_context", now_ns)
            return
        if max(row.ts_init for row in batch) > now_ns:
            return
        not_before, expires = factor_execution_window(
            date.fromisoformat(expected), self._settings.signal_expiry_hours
        )
        if now >= expires:
            self._audit(expected, "SKIP", "factor_execution_window_expired", now_ns)
            return
        if self._settings.bootstrap_from_catalog:
            receipt = self._require_repository().get_factor_import(
                catalog_path=self._settings.catalog_path,
                delivery_id=representative.delivery_id,
                mode="paper",
            )
            if (
                receipt is None
                or receipt.verified_at >= not_before
                or (
                    receipt.calendar_version != CALENDAR_VERSION
                    or receipt.model_release_id != self._settings.model_release_id
                )
            ):
                self._audit(expected, "SKIP", "missing_timely_factor_acceptance", now_ns)
                return
        scores = {
            value.canonical_id: value.score if value.eligible else None
            for value in batch
            if value.canonical_id in self._expected_instruments
        }
        decision = calculate_factor_weights(
            scores,
            top_n=self._settings.top_n,
            target_gross_exposure=self._settings.target_gross_exposure,
            max_unscorable_fraction=self._settings.max_unscorable_fraction,
        )
        context = FactorContext(
            representative.asof_date,
            representative.delivery_id,
            representative.model_release_id,
            representative.source_kind,
            representative.calendar_version,
            tuple(sorted(scores)),
            decision.eligible_count,
            self._settings.catalog_path,
        )
        self._audit(
            expected,
            decision.status,
            decision.reason,
            now_ns,
            preserve=decision.preserve_positions,
            context=context,
        )
        if decision.status == "SKIP":
            return
        timestamp_ns = max(value.ts_init for value in batch)
        event = TradeSignalEvent(
            strategy_name=self._settings.strategy_name,
            target_weights=decision.target_weights,
            preserve_positions=decision.preserve_positions,
            factor_context=context,
            not_before_ns=int(not_before.timestamp() * 1_000_000_000),
            rebalance_key=f"{representative.model_release_id}:{representative.asof_date}",
            reason=(
                f"PatchTST factor as of {representative.asof_date}; "
                f"top_n={self._settings.top_n}; release={representative.model_release_id}"
            ),
            expires_at_ns=int(expires.timestamp() * 1_000_000_000),
            ts_event=timestamp_ns,
            ts_init=max(self.clock.timestamp_ns(), timestamp_ns),
        )
        repository = self._require_repository()
        workflow, created = repository.register_signal_workflow(
            event,
            scope=self._settings.signal_scope,
        )
        published_event = workflow.to_event()
        if created:
            repository.record_signal(published_event)
        self.msgbus.publish(self._settings.signal_topic, published_event)

    def _require_repository(self) -> TradingRepository:
        if self._repository is None:
            raise RuntimeError("Actor repository is not initialized")
        return self._repository

    def _audit(
        self,
        day: str,
        status: str,
        reason: str,
        now_ns: int,
        *,
        preserve: tuple[str, ...] = (),
        context: FactorContext | None = None,
    ) -> None:
        self._require_repository().record_factor_decision(
            scope=self._settings.signal_scope,
            strategy_name=self._settings.strategy_name,
            asof_date=day,
            status=status,
            reason=reason,
            timestamp_ns=now_ns,
            preserve_positions=preserve,
            context=context,
        )

    def _check_factors(self, _: TimeEvent | None) -> None:
        if self.clock.timestamp_ns() < self._settings.publish_after_ns:
            return
        now_ns = self.clock.timestamp_ns()
        expected = expected_factor_date(datetime.fromtimestamp(now_ns / 1e9, tz=UTC))
        if self._scheduled_check_date != expected:
            # NT 可先将提醒移入待执行队列;不能仅凭 timer_names 判断是否已排期。
            window = factor_execution_window(expected, self._settings.signal_expiry_hours)
            for boundary, instant in zip(("open", "expiry"), window, strict=True):
                name = f"factor-boundary-{boundary}-{expected}"
                timestamp_ns = int(instant.timestamp() * 1e9)
                if timestamp_ns > now_ns:
                    self.clock.set_time_alert_ns(name, timestamp_ns, callback=self._check_factors)
            self._scheduled_check_date = expected
        if self._settings.bootstrap_from_catalog:
            self._request_catalog_history()
        else:
            self._catalog_request_completed(UUID4())

    def _request_catalog_history(self) -> None:
        now_ns = self.clock.timestamp_ns()
        if (
            self._request_started_ns is not None
            and now_ns - self._request_started_ns < 60_000_000_000
        ):
            return
        if self._request_outcome is not None:
            self._request_outcome.status = "cancelled"
        outcome = CatalogRequestOutcome()
        self._request_outcome = outcome
        self._request_rows.clear()
        self._pending_batches.clear()
        self._batch_sizes.clear()
        self._request_started_ns = now_ns
        self._request_generation += 1
        end = self.clock.utc_now()
        start = end - timedelta(days=self._settings.catalog_lookback_days)
        try:
            self.request_data(
                FACTOR_DATA_TYPE,
                client_id=ClientId(self._settings.catalog_client_id),
                start=start,
                end=end,
                callback=partial(
                    self._catalog_request_completed,
                    generation=self._request_generation,
                    outcome=outcome,
                ),
                params={"catalog_outcome": outcome},
            )
        except Exception as exc:
            outcome.status = "cancelled"
            self._request_started_ns = None
            self._request_generation += 1
            self.log.error(f"Factor history request failed: {type(exc).__name__}")

    def _catalog_request_completed(
        self,
        _: UUID4,
        *,
        generation: int | None = None,
        outcome: CatalogRequestOutcome | None = None,
    ) -> None:
        if generation is not None and (
            generation != self._request_generation or self._request_started_ns is None
        ):
            return
        self._request_started_ns = None
        if self._settings.bootstrap_from_catalog and (outcome is None or outcome.status != "ok"):
            self.log.warning("Factor history is unavailable; waiting for the next check")
            return
        now_ns = self.clock.timestamp_ns()
        now = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=UTC)
        expected = expected_factor_date(now)
        batches = [
            batch
            for batch in self._completed_batches.values()
            if batch[0].asof_date == expected.isoformat()
            and batch[0].model_release_id == self._settings.model_release_id
            and (
                not self._settings.bootstrap_from_catalog
                or self._request_rows.get(batch[0].batch_id) == {row.canonical_id for row in batch}
            )
        ]
        if batches:
            self._publish_signal(max(batches, key=lambda batch: batch[0].ts_init))
        elif now >= datetime.combine(expected + timedelta(days=1), time.min, tzinfo=UTC):
            self._audit(expected.isoformat(), "SKIP", "missing_expected_factor_batch", now_ns)

    def on_reset(self) -> None:
        """清空聚合状态以支持 NT 重置。"""
        self._pending_batches.clear()
        self._batch_sizes.clear()
        self._completed_batches.clear()
        self._scheduled_check_date = None
        self._request_started_ns = None
        self._request_generation += 1
        self._request_rows.clear()
        if self._request_outcome is not None:
            self._request_outcome.status = "cancelled"

    def on_stop(self) -> None:
        """取消订阅并关闭审计仓储。"""
        self._request_generation += 1
        if self._request_outcome is not None:
            self._request_outcome.status = "cancelled"
        for name in self.clock.timer_names:
            if name == "factor-check" or name.startswith("factor-boundary-"):
                self.clock.cancel_timer(name)
        if self._settings.stream_data:
            self.unsubscribe_data(
                FACTOR_DATA_TYPE,
                client_id=ClientId(self._settings.data_client_id),
            )
        if self._repository is not None:
            self._repository.close()
            self._repository = None
