"""回测中的现金分红与 NT 账户权益快照模块。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.common.component import Logger
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import AccountId, InstrumentId
from nautilus_trader.model.objects import Currency, Money

from trading_assistant.data.corporate_actions import CorporateActionRepository


class DividendSimulationConfig(SimulationModuleConfig, frozen=True):
    """公司行动目录、执行 BarType 和快照输出配置。"""

    corporate_action_directory: str
    instrument_ids: tuple[str, ...]
    execution_bar_types: dict[str, str]
    account_id: str
    snapshot_path: str
    currency: str = "USD"


class DividendSimulationModule(SimulationModule):  # type: ignore[misc]
    """按当前拆股口径入账现金分红, 并记录 NT 账户权益状态。"""

    def __init__(self, config: DividendSimulationConfig) -> None:
        super().__init__(config)
        self._settings = config
        repository = CorporateActionRepository(Path(config.corporate_action_directory))
        self._dividends = {
            (instrument_id, item.ex_date): item
            for instrument_id in config.instrument_ids
            for item in repository.read(instrument_id).dividends
        }
        self._execution_types = {
            value: instrument_id for instrument_id, value in config.execution_bar_types.items()
        }
        self._currency = Currency.from_str(config.currency)
        self._account_id = AccountId(config.account_id)
        self._latest_prices: dict[str, float] = {}
        self._applied_dividends: set[tuple[str, date]] = set()
        self._snapshots: dict[int, dict[str, object]] = {}
        self._snapshot_due = False
        self._session_date: date | None = None
        self._dividend_cashflow = Decimal(0)

    def pre_process(self, data: Data) -> None:
        """在撮合前按除息日持仓入账, 并更新执行价格。"""
        if not isinstance(data, Bar):
            return
        canonical_id = self._execution_types.get(str(data.bar_type))
        if canonical_id is None:
            return
        self._latest_prices[canonical_id] = data.close.as_double()
        self._snapshot_due = True

        trading_day = datetime.fromtimestamp(
            data.ts_event / 1_000_000_000,
            tz=UTC,
        ).date()
        self._session_date = trading_day
        key = (canonical_id, trading_day)
        dividend = self._dividends.get(key)
        if dividend is None or key in self._applied_dividends:
            return
        quantity = self._position_quantity(canonical_id)
        cashflow = Decimal(str(quantity)) * dividend.value
        if cashflow != 0:
            self.exchange.adjust_account(Money(float(cashflow), self._currency))
            self._dividend_cashflow += cashflow
        self._applied_dividends.add(key)

    def process(self, ts_now: int) -> None:
        """撮合结算后从 NT 账户与持仓形成单一权益快照。"""
        if not self._snapshot_due:
            return
        account = self.exchange.get_account()
        if account.id != self._account_id:
            raise RuntimeError(
                f"Unexpected backtest account: expected={self._account_id}, actual={account.id}"
            )
        cash = account.balance_total(self._currency).as_double()
        market_value = 0.0
        for canonical_id in self._settings.instrument_ids:
            quantity = self._position_quantity(canonical_id)
            if quantity == 0:
                continue
            price = self._latest_prices.get(canonical_id)
            if price is None:
                raise RuntimeError(f"Missing execution price for open position: {canonical_id}")
            market_value += quantity * price
        self._snapshots[ts_now] = {
            "timestamp_ns": ts_now,
            "session_date": (
                None if self._session_date is None else self._session_date.isoformat()
            ),
            "cash": cash,
            "market_value": market_value,
            "equity": cash + market_value,
            "dividend_cashflow": float(self._dividend_cashflow),
        }
        self._snapshot_due = False
        self._session_date = None
        self._dividend_cashflow = Decimal(0)

    def _position_quantity(self, canonical_id: str) -> float:
        instrument_id = InstrumentId.from_str(canonical_id)
        positions = self.exchange.cache.positions_open(
            instrument_id=instrument_id,
            account_id=self._account_id,
        )
        return sum(float(position.signed_qty) for position in positions)

    def _write_snapshots(self) -> None:
        """以稳定 JSON 写出逐日 NT 状态, 供报告层消费。"""
        path = Path(self._settings.snapshot_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [self._snapshots[key] for key in sorted(self._snapshots)],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def log_diagnostics(self, logger: Logger) -> None:
        """在 NT 回测结束钩子写出快照并输出处理规模。"""
        self._write_snapshots()
        logger.info(
            f"Dividend simulation snapshots={len(self._snapshots)}, "
            f"actions={len(self._applied_dividends)}"
        )

    def reset(self) -> None:
        """清空运行期状态。"""
        self._latest_prices.clear()
        self._applied_dividends.clear()
        self._snapshots.clear()
        self._snapshot_due = False
        self._session_date = None
        self._dividend_cashflow = Decimal(0)
