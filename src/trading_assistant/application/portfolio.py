"""账户与持仓的只读查询服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from nautilus_trader.model.data import BarType
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.application.models import (
    AccountHistoryPoint,
    Page,
    PortfolioView,
    PositionView,
    QuerySourceError,
    SourceState,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.storage.repository import TradingRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


def mask_account_id(account_id: str) -> str:
    """只保留账户类别和末四位。"""
    prefix, separator, value = account_id.partition("-")
    suffix = value if separator else prefix
    visible = suffix[-4:] if len(suffix) >= 4 else suffix[-1:]
    masked = f"••••{visible}"
    return f"{prefix}-{masked}" if separator else masked


class PortfolioQueryService:
    """组合账户快照、标的映射和 EOD 参考价格。"""

    def __init__(
        self,
        *,
        repository: TradingRepository | None,
        account_id: str | None,
        catalog_path: Path,
        instruments: tuple[InstrumentSpec, ...],
        execution_bar_type_suffix: str,
        stale_after_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._account_id = account_id
        self._catalog_path = catalog_path
        self._instruments = instruments
        self._execution_bar_type_suffix = execution_bar_type_suffix
        self._stale_after_seconds = stale_after_seconds
        self._clock = clock

    def latest(self) -> PortfolioView:
        """读取最近一次完整账户快照。"""
        now = self._clock().astimezone(UTC)
        if self._account_id is None:
            return self._empty("unconfigured", now)
        if self._repository is None:
            return self._empty("missing", now)
        try:
            snapshot = self._repository.latest_portfolio_snapshot(account_id=self._account_id)
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "账户审计库无法读取") from exc
        if snapshot is None:
            return self._empty("empty", now)

        age_seconds = max((now - snapshot.timestamp_utc).total_seconds(), 0.0)
        reason = snapshot.not_ready_reason
        if age_seconds > self._stale_after_seconds:
            reason = "local snapshot stale"
        elif snapshot.broker_connected is not True:
            reason = "broker disconnected or connection unknown"
        elif snapshot.reconciliation_complete is not True:
            reason = "broker reconciliation incomplete"
        elif snapshot.account_updated_at_utc is None or snapshot.broker_stale_after_seconds is None:
            reason = "broker account update unavailable"
        elif (
            not 0
            <= (now - snapshot.account_updated_at_utc).total_seconds()
            <= snapshot.broker_stale_after_seconds
        ):
            reason = "broker account update stale or future"
        by_source_id = {spec.resolved_live_instrument_id: spec for spec in self._instruments} | {
            spec.canonical_id: spec for spec in self._instruments
        }
        catalog = CatalogRepository(self._catalog_path) if self._catalog_path.is_dir() else None
        positions: list[PositionView] = []
        for position in snapshot.positions:
            spec = by_source_id.get(position.instrument_id)
            canonical_id = position.instrument_id if spec is None else spec.canonical_id
            symbol = canonical_id.split(".", maxsplit=1)[0] if spec is None else spec.symbol
            price, price_at = self._latest_reference_price(catalog, canonical_id)
            market_value = None if price is None else position.signed_quantity * price
            weight = (
                None
                if market_value is None or snapshot.net_liquidation == 0
                else market_value / snapshot.net_liquidation
            )
            positions.append(
                PositionView(
                    canonical_id=canonical_id,
                    source_instrument_id=position.instrument_id,
                    symbol=symbol,
                    side=position.side,
                    signed_quantity=position.signed_quantity,
                    avg_open_price=position.avg_open_price,
                    realized_pnl=position.realized_pnl,
                    reference_price=price,
                    reference_price_at_utc=price_at,
                    estimated_market_value=market_value,
                    estimated_weight=weight,
                    price_kind=None if price is None else "EOD_EXTERNAL",
                )
            )
        return PortfolioView(
            source_state="available",
            observed_at_utc=now,
            snapshot_at_utc=snapshot.timestamp_utc,
            account_id=mask_account_id(snapshot.account_id),
            currency=snapshot.currency,
            net_liquidation=snapshot.net_liquidation,
            available_funds=snapshot.available_funds,
            total_cash_value=snapshot.total_cash_value,
            age_seconds=age_seconds,
            is_stale=reason is not None,
            account_updated_at_utc=snapshot.account_updated_at_utc,
            broker_connected=snapshot.broker_connected,
            reconciliation_complete=snapshot.reconciliation_complete,
            broker_stale_after_seconds=snapshot.broker_stale_after_seconds,
            not_ready_reason=reason,
            positions=tuple(positions),
        )

    def history(self, *, offset: int, limit: int) -> Page[AccountHistoryPoint]:
        """分页读取账户资金历史。"""
        if self._repository is None or self._account_id is None:
            return Page(items=(), offset=offset, limit=limit, has_more=False)
        try:
            rows = self._repository.list_account_snapshots(
                account_id=self._account_id,
                limit=limit + 1,
                offset=offset,
            )
        except SQLAlchemyError as exc:
            raise QuerySourceError("live_database", "账户审计库无法读取") from exc
        items = tuple(
            AccountHistoryPoint(
                timestamp_utc=row.timestamp_utc,
                account_id=mask_account_id(row.account_id),
                currency=row.currency,
                net_liquidation=row.net_liquidation,
                available_funds=row.available_funds,
                total_cash_value=row.total_cash_value,
                account_updated_at_utc=row.account_updated_at_utc,
                broker_connected=row.broker_connected,
                reconciliation_complete=row.reconciliation_complete,
                broker_stale_after_seconds=row.broker_stale_after_seconds,
                not_ready_reason=row.not_ready_reason,
            )
            for row in rows[:limit]
        )
        return Page(items=items, offset=offset, limit=limit, has_more=len(rows) > limit)

    def _latest_reference_price(
        self,
        catalog: CatalogRepository | None,
        canonical_id: str,
    ) -> tuple[float | None, datetime | None]:
        if catalog is None:
            return None, None
        try:
            bar_type = BarType.from_str(f"{canonical_id}-{self._execution_bar_type_suffix}")
            latest_ns = catalog.latest_bar_timestamp(bar_type)
            if latest_ns is None:
                return None, None
            bars = catalog.read_bars(bar_type, start_ns=latest_ns)
        except Exception:  # NT Catalog 的底层异常类型并不稳定。
            return None, None
        if not bars:
            return None, None
        bar = bars[-1]
        timestamp = datetime.fromtimestamp(bar.ts_init / 1_000_000_000, tz=UTC)
        return bar.close.as_double(), timestamp

    @staticmethod
    def _empty(state: SourceState, observed_at: datetime) -> PortfolioView:
        if state not in {"missing", "empty", "unconfigured"}:
            raise ValueError(f"Unexpected portfolio state: {state}")
        return PortfolioView(
            source_state=state,
            observed_at_utc=observed_at,
            snapshot_at_utc=None,
            account_id=None,
            currency=None,
            net_liquidation=None,
            available_funds=None,
            total_cash_value=None,
            age_seconds=None,
            is_stale=None,
            positions=(),
        )
