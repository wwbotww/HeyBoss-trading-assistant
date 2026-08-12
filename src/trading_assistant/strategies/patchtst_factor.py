"""回测与 paper 共用的 PatchTST 因子决策 Actor。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import ClientId

from trading_assistant.data.factor import FACTOR_DATA_TYPE, FactorScoreData
from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, TradeSignalEvent
from trading_assistant.signals.factor import calculate_factor_weights
from trading_assistant.storage.repository import TradingRepository

NANOSECONDS_PER_HOUR = 3_600_000_000_000


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


class PatchTSTFactorActor(Actor):  # type: ignore[misc]
    """聚合完整因子横截面并发布目标权重。"""

    def __init__(self, config: PatchTSTFactorActorConfig) -> None:
        super().__init__(config)
        self._settings = config
        self._expected_instruments = frozenset(config.instrument_ids)
        self._pending_batches: dict[str, dict[str, FactorScoreData]] = {}
        self._batch_sizes: dict[str, int] = {}
        self._completed_batches: dict[str, tuple[FactorScoreData, ...]] = {}
        self._repository: TradingRepository | None = None

    def on_start(self) -> None:
        """建立审计仓储并装配 NT CustomData 输入。"""
        self._repository = TradingRepository(self._settings.database_url)
        self._repository.create_schema()
        if self._settings.stream_data:
            self.subscribe_data(
                FACTOR_DATA_TYPE,
                client_id=ClientId(self._settings.data_client_id),
            )
        if self._settings.bootstrap_from_catalog:
            self._request_catalog_history()

    def on_data(self, data: object) -> None:
        """处理 BacktestNode 流式回放的 FactorScoreData。"""
        if isinstance(data, FactorScoreData):
            self._ingest_factor(data, publish=True)

    def on_historical_data(self, data: object) -> None:
        """处理 TradingNode 从同一 Catalog 请求的历史 FactorScoreData。"""
        if isinstance(data, FactorScoreData):
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
        scores = {
            value.canonical_id: value.score
            for value in batch
            if value.eligible and value.canonical_id in self._expected_instruments
        }
        weights = calculate_factor_weights(
            scores,
            top_n=self._settings.top_n,
            target_gross_exposure=self._settings.target_gross_exposure,
        )
        timestamp_ns = max(value.ts_event for value in batch)
        event = TradeSignalEvent(
            strategy_name=self._settings.strategy_name,
            target_weights=tuple(sorted(weights.items())),
            rebalance_key=f"{representative.model_release_id}:{representative.asof_date}",
            reason=(
                f"PatchTST factor as of {representative.asof_date}; "
                f"top_n={self._settings.top_n}; release={representative.model_release_id}"
            ),
            expires_at_ns=(
                timestamp_ns + self._settings.signal_expiry_hours * NANOSECONDS_PER_HOUR
            ),
            ts_event=timestamp_ns,
            ts_init=max(self.clock.timestamp_ns(), timestamp_ns),
        )
        if self._repository is None:
            raise RuntimeError("Actor repository is not initialized")
        workflow, created = self._repository.register_signal_workflow(
            event,
            scope=self._settings.signal_scope,
        )
        published_event = workflow.to_event()
        if created:
            self._repository.record_signal(published_event)
        self.msgbus.publish(self._settings.signal_topic, published_event)

    def _request_catalog_history(self) -> None:
        end = datetime.fromtimestamp(self.clock.timestamp_ns() / 1_000_000_000, tz=UTC)
        start = end - timedelta(days=self._settings.catalog_lookback_days)
        self.request_data(
            FACTOR_DATA_TYPE,
            client_id=ClientId(self._settings.catalog_client_id),
            start=start,
            end=end,
            callback=self._catalog_request_completed,
        )

    def _catalog_request_completed(self, _: UUID4) -> None:
        if not self._completed_batches:
            self.log.error("Catalog bootstrap returned no complete factor batches")
            return
        latest_batch = max(
            self._completed_batches.values(),
            key=lambda batch: max(value.ts_event for value in batch),
        )
        self._publish_signal(latest_batch)

    def on_reset(self) -> None:
        """清空聚合状态以支持 NT 重置。"""
        self._pending_batches.clear()
        self._batch_sizes.clear()
        self._completed_batches.clear()

    def on_stop(self) -> None:
        """取消订阅并关闭审计仓储。"""
        if self._settings.stream_data:
            self.unsubscribe_data(
                FACTOR_DATA_TYPE,
                client_id=ClientId(self._settings.data_client_id),
            )
        if self._repository is not None:
            self._repository.close()
            self._repository = None
