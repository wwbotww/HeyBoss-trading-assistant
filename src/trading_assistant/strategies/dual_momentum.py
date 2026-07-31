"""使用 NT 原生 Bar 与 MessageBus 的双动量决策 Actor。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import ClientId

from trading_assistant.execution.events import TRADE_SIGNAL_TOPIC, TradeSignalEvent
from trading_assistant.signals.momentum import calculate_dual_momentum_weights
from trading_assistant.storage.repository import TradingRepository

NANOSECONDS_PER_HOUR = 3_600_000_000_000


class DualMomentumActorConfig(ActorConfig, frozen=True):
    """双动量决策 Actor 配置。"""

    bar_types: tuple[str, ...]
    instrument_ids: tuple[str, ...]
    lookback_months: int
    top_n: int
    fallback_instrument: str
    signal_expiry_hours: int
    database_url: str
    strategy_name: str = "dual_momentum"
    signal_topic: str = TRADE_SIGNAL_TOPIC
    signal_scope: str = "default"
    stream_bars: bool = True
    bootstrap_from_catalog: bool = False
    catalog_lookback_days: int = 2_200
    catalog_client_id: str = "CATALOG"
    bootstrap_bar_types: tuple[str, ...] = ()
    publish_after_ns: int = 0


class DualMomentumActor(Actor):  # type: ignore[misc]
    """收集完整日线、形成月末快照并发布目标权重。"""

    def __init__(self, config: DualMomentumActorConfig) -> None:
        super().__init__(config)
        self._settings = config
        self._expected_instruments = frozenset(config.instrument_ids)
        self._signal_bar_types = frozenset(config.bar_types)
        self._pending_sessions: dict[int, dict[str, float]] = {}
        self._monthly_closes: dict[str, dict[str, float]] = {}
        self._active_month: str | None = None
        self._repository: TradingRepository | None = None
        self._catalog_requests: set[str] = set()

    def on_start(self) -> None:
        """建立审计仓储并订阅 M1 的原生日线 BarType。"""
        self._repository = TradingRepository(self._settings.database_url)
        self._repository.create_schema()
        if self._settings.stream_bars:
            for value in self._settings.bar_types:
                self.subscribe_bars(BarType.from_str(value))
        if self._settings.bootstrap_from_catalog:
            self._request_catalog_history()

    def on_bar(self, bar: Bar) -> None:
        """按时间戳等待全部标的日线到齐后推进月度状态。"""
        self._ingest_bar(bar, publish_transitions=True)

    def on_historical_data(self, data: object) -> None:
        """接收 DataEngine 从同一 Catalog 返回的原生历史 Bar。"""
        if isinstance(data, Bar):
            self._ingest_bar(data, publish_transitions=False)

    def _ingest_bar(self, bar: Bar, *, publish_transitions: bool) -> None:
        if str(bar.bar_type) not in self._signal_bar_types:
            return
        instrument_id = str(bar.bar_type.instrument_id)
        if instrument_id not in self._expected_instruments:
            return
        prices = self._pending_sessions.setdefault(bar.ts_init, {})
        prices[instrument_id] = bar.close.as_double()
        if prices.keys() >= self._expected_instruments:
            self._process_complete_session(
                bar.ts_init,
                prices,
                publish_transitions=publish_transitions,
            )
            del self._pending_sessions[bar.ts_init]

    def _process_complete_session(
        self,
        timestamp_ns: int,
        prices: dict[str, float],
        *,
        publish_transitions: bool = True,
    ) -> None:
        session_month = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC).strftime(
            "%Y-%m"
        )
        if (
            publish_transitions
            and self._active_month is not None
            and session_month != self._active_month
            and timestamp_ns >= self._settings.publish_after_ns
        ):
            self._publish_signal(timestamp_ns, self._active_month)
        self._active_month = session_month
        self._monthly_closes.setdefault(session_month, {}).update(prices)

    def _publish_signal(self, timestamp_ns: int, as_of_month: str) -> None:
        closes = pd.DataFrame.from_dict(self._monthly_closes, orient="index").sort_index()
        closes = closes.loc[closes.index <= as_of_month]
        weights = calculate_dual_momentum_weights(
            closes,
            lookback_months=self._settings.lookback_months,
            top_n=self._settings.top_n,
            fallback_instrument=self._settings.fallback_instrument,
        )
        event = TradeSignalEvent(
            strategy_name=self._settings.strategy_name,
            target_weights=tuple(sorted(weights.items())),
            rebalance_key=as_of_month,
            reason=(
                f"{self._settings.lookback_months}-month price momentum "
                f"as of {as_of_month}; top_n={self._settings.top_n}"
            ),
            expires_at_ns=(
                timestamp_ns + self._settings.signal_expiry_hours * NANOSECONDS_PER_HOUR
            ),
            ts_event=timestamp_ns,
            ts_init=self.clock.timestamp_ns(),
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
        """通过 NT DataEngine 向 Catalog 请求策略启动所需历史 Bar。"""
        end = datetime.fromtimestamp(self.clock.timestamp_ns() / 1_000_000_000, tz=UTC)
        start = end - timedelta(days=self._settings.catalog_lookback_days)
        requested_bar_types = tuple(
            dict.fromkeys((*self._settings.bar_types, *self._settings.bootstrap_bar_types))
        )
        self._catalog_requests = set(requested_bar_types)
        client_id = ClientId(self._settings.catalog_client_id)
        for value in requested_bar_types:
            self.request_bars(
                BarType.from_str(value),
                start=start,
                end=end,
                client_id=client_id,
                callback=partial(self._catalog_request_completed, value),
            )

    def _catalog_request_completed(self, bar_type: str, _: UUID4) -> None:
        self._catalog_requests.discard(bar_type)
        if self._catalog_requests:
            return
        self._emit_latest_catalog_signal()

    def _emit_latest_catalog_signal(self) -> None:
        """只为最新完整日历月发布一次可执行信号。"""
        if not self._monthly_closes:
            self.log.error("Catalog bootstrap returned no complete sessions")
            return
        current_month = datetime.fromtimestamp(
            self.clock.timestamp_ns() / 1_000_000_000,
            tz=UTC,
        ).strftime("%Y-%m")
        eligible_months = sorted(month for month in self._monthly_closes if month < current_month)
        if not eligible_months:
            self.log.error("Catalog bootstrap has no completed calendar month")
            return
        self._publish_signal(self.clock.timestamp_ns(), eligible_months[-1])

    def on_reset(self) -> None:
        """清空可变状态以支持 NT 重置。"""
        self._pending_sessions.clear()
        self._monthly_closes.clear()
        self._active_month = None
        self._catalog_requests.clear()

    def on_stop(self) -> None:
        """取消订阅并释放数据库连接。"""
        if self._settings.stream_bars:
            for value in self._settings.bar_types:
                self.unsubscribe_bars(BarType.from_str(value))
        if self._repository is not None:
            self._repository.close()
            self._repository = None
