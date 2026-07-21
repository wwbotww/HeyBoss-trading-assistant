"""从 TradingNode 的 NT 账户与 Cache 采集只读组合快照。"""

from __future__ import annotations

from datetime import timedelta

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.position import Position

from trading_assistant.storage.repository import PositionSnapshotInput, TradingRepository

SNAPSHOT_TIMER_NAME = "portfolio-snapshot"


class PortfolioSnapshotActorConfig(ActorConfig, frozen=True):
    """账户快照 Actor 配置。"""

    account_id: str
    database_url: str
    snapshot_interval_seconds: int = 30
    currency: str = "USD"


def position_snapshot_input(position: Position) -> PositionSnapshotInput:
    """把 NT 原生 Position 转换为持久化边界输入。"""
    realized_pnl = position.realized_pnl
    return PositionSnapshotInput(
        instrument_id=str(position.instrument_id),
        signed_quantity=float(position.signed_qty),
        side=str(position.side),
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
        repository.record_portfolio_snapshot(
            timestamp_ns=self.clock.timestamp_ns(),
            account_id=str(self._account_id),
            currency=str(self._currency),
            net_liquidation=account.balance_total(self._currency).as_double(),
            free_cash=account.balance_free(self._currency).as_double(),
            locked_cash=account.balance_locked(self._currency).as_double(),
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
