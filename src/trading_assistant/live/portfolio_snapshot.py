"""从 TradingNode 的 NT 账户与 Cache 采集只读组合快照。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from math import isfinite

from nautilus_trader.accounting.accounts.base import Account
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.position import Position

from trading_assistant.storage.repository import (
    PositionSnapshotInput,
    TradingRepository,
    utc_datetime_from_ns,
)

SNAPSHOT_TIMER_NAME = "portfolio-snapshot"


def account_update(account: Account | None, currency: Currency) -> AccountState | None:
    """取对应币种的真实账户回报, 不把其他币种的现金或时钟混入 USD。"""
    if account is None:
        return None
    return next(
        (
            event
            for event in reversed(account.events)
            if any(balance.currency == currency for balance in event.balances)
        ),
        None,
    )


def account_not_ready_reason(
    account: Account | None,
    *,
    now_ns: int,
    stale_after_seconds: int,
    broker_connected: bool | None,
    reconciliation_complete: bool | None,
) -> str | None:
    """Gateway 与快照共用券商时钟语义, 本地采样不刷新来源时间。"""
    if broker_connected is not True:
        return "broker disconnected or connection unknown"
    if reconciliation_complete is not True:
        return "broker reconciliation incomplete"
    update = account_update(account, Currency.from_str("USD"))
    if update is None:
        return "broker account update unavailable"
    age_ns = now_ns - update.ts_event
    if not 0 <= age_ns <= stale_after_seconds * 1_000_000_000:
        return "broker account update stale or future"
    return None


class PortfolioSnapshotActorConfig(ActorConfig, frozen=True):
    """账户快照 Actor 配置。"""

    account_id: str
    database_url: str
    snapshot_interval_seconds: int = 30
    currency: str = "USD"
    broker_account_stale_after_seconds: int = 300


def position_snapshot_input(position: Position) -> PositionSnapshotInput:
    """把 NT 原生 Position 转换为持久化边界输入。"""
    realized_pnl = position.realized_pnl
    return PositionSnapshotInput(
        instrument_id=str(position.instrument_id),
        signed_quantity=float(position.signed_qty),
        side=position.side.name,
        avg_open_price=float(position.avg_px_open),
        realized_pnl=None if realized_pnl is None else realized_pnl.as_double(),
    )


class PortfolioSnapshotActor(Actor):  # type: ignore[misc]
    """在 NT 进程内定时落盘资金与开仓仓位, 不参与交易。"""

    def __init__(self, config: PortfolioSnapshotActorConfig) -> None:
        super().__init__(config)
        self._settings = config
        self._account_id = AccountId(config.account_id)
        self._currency = Currency.from_str(config.currency)
        self._repository: TradingRepository | None = None
        self._broker_status: Callable[[], tuple[bool, bool]] | None = None

    def bind_broker_status(self, status: Callable[[], tuple[bool, bool]]) -> None:
        """由节点装配共享的本次连接与核对状态。"""
        self._broker_status = status

    def on_start(self) -> None:
        """创建快照表并启动定时采集。"""
        self._repository = TradingRepository(self._settings.database_url)
        self._repository.create_schema()
        self.clock.set_timer(
            SNAPSHOT_TIMER_NAME,
            interval=timedelta(seconds=self._settings.snapshot_interval_seconds),
            callback=self._capture_snapshot,
            fire_immediately=True,
        )

    def _capture_snapshot(self, _: TimeEvent) -> None:
        repository = self._repository
        if repository is None:
            raise RuntimeError("Portfolio snapshot repository is not initialized")
        account = self.portfolio.account(account_id=self._account_id)
        if account is None:
            self.log.warning(f"Account not available for snapshot: account_id={self._account_id}")
            return
        positions = tuple(
            position_snapshot_input(position)
            for position in self.cache.positions_open(account_id=self._account_id)
        )
        connected, reconciled = (
            (None, None) if self._broker_status is None else self._broker_status()
        )
        now_ns = self.clock.timestamp_ns()
        last_event = account_update(account, self._currency)
        total = account.balance_total(self._currency)
        available = account.balance_free(self._currency)
        if last_event is None or total is None:
            self.log.warning(f"Account currency not available for snapshot: {self._currency}")
            return
        # IBKR 在 AccountState.info 提供 TotalCashValue; 缺失不拿可用资金替代。
        raw_cash = last_event.info.get("TotalCashValue")
        cash = float(raw_cash) if isinstance(raw_cash, (int, float)) else None
        if cash is not None and not isfinite(cash):
            cash = None
        repository.record_portfolio_snapshot(
            timestamp_ns=now_ns,
            account_id=str(self._account_id),
            currency=str(self._currency),
            net_liquidation=total.as_double(),
            available_funds=None if available is None else available.as_double(),
            total_cash_value=cash,
            account_updated_at_utc=utc_datetime_from_ns(last_event.ts_event),
            broker_connected=connected,
            reconciliation_complete=reconciled,
            broker_stale_after_seconds=self._settings.broker_account_stale_after_seconds,
            not_ready_reason=account_not_ready_reason(
                account,
                now_ns=now_ns,
                stale_after_seconds=self._settings.broker_account_stale_after_seconds,
                broker_connected=connected,
                reconciliation_complete=reconciled,
            ),
            positions=positions,
        )
        self.log.info(
            "Portfolio snapshot recorded: "
            f"account_id={self._account_id}, positions={len(positions)}"
        )

    def on_stop(self) -> None:
        """停止定时器并释放数据库连接。"""
        if SNAPSHOT_TIMER_NAME in self.clock.timer_names:
            self.clock.cancel_timer(SNAPSHOT_TIMER_NAME)
        if self._repository is not None:
            self._repository.close()
            self._repository = None
