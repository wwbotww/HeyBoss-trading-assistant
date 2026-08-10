"""Dashboard 的只读数据装配与事后收益计算。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from nautilus_trader.model.data import BarType
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.storage.repository import PortfolioSnapshot, SignalReview, TradingRepository

SIGNAL_COLUMNS = (
    "timestamp_utc",
    "event_id",
    "strategy_name",
    "instrument_id",
    "direction",
    "target_weight",
    "status",
    "reason",
    "risk_summary",
    "planned_orders",
    "forward_5_sessions",
    "forward_10_sessions",
    "forward_20_sessions",
)
DECISION_COLUMNS = (
    "timestamp_utc",
    "event_id",
    "strategy_name",
    "approval_mode",
    "decision",
    "reason",
)
ORDER_COLUMNS = (
    "timestamp_utc",
    "event_id",
    "instrument_id",
    "client_order_id",
    "status",
    "direction",
    "quantity",
    "reason",
)


@dataclass(frozen=True)
class AuditViews:
    """Dashboard 一次刷新读取到的审计视图。"""

    portfolio: PortfolioSnapshot | None
    signals: tuple[SignalReview, ...]
    decisions: pd.DataFrame
    orders: pd.DataFrame


@dataclass(frozen=True)
class BacktestReportView:
    """一个可浏览的回测报告目录。"""

    run_id: str
    summary: dict[str, Any]
    returns: pd.DataFrame
    fills: pd.DataFrame


def mask_account_id(account_id: str) -> str:
    """仅保留账户标识首尾, 避免看板泄露完整账号。"""
    if len(account_id) <= 6:
        return "*" * len(account_id)
    return f"{account_id[:3]}{'*' * (len(account_id) - 6)}{account_id[-3:]}"


def _database_available(database_url: str) -> bool:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return True
    value = database_url.removeprefix(prefix)
    if value == ":memory:":
        return True
    return Path(value).expanduser().exists()


def load_audit_views(*, database_url: str, account_id: str, scope: str) -> AuditViews:
    """只读加载 SQLite 审计数据; 未初始化时返回空视图。"""
    empty = AuditViews(
        portfolio=None,
        signals=(),
        decisions=pd.DataFrame(columns=DECISION_COLUMNS),
        orders=pd.DataFrame(columns=ORDER_COLUMNS),
    )
    if not _database_available(database_url):
        return empty
    repository = TradingRepository(database_url, read_only=True)
    try:
        portfolio = repository.latest_portfolio_snapshot(account_id=account_id)
        signals = repository.list_signal_reviews(scope=scope)
        decisions = pd.DataFrame.from_records(
            [audit.__dict__ for audit in repository.list_decision_audits(scope=scope)],
            columns=DECISION_COLUMNS,
        )
        orders = pd.DataFrame.from_records(
            [audit.__dict__ for audit in repository.list_order_audits(scope=scope)],
            columns=ORDER_COLUMNS,
        )
    except SQLAlchemyError:
        return empty
    finally:
        repository.close()
    return AuditViews(
        portfolio=portfolio,
        signals=signals,
        decisions=decisions,
        orders=orders,
    )


def signal_review_frame(
    reviews: tuple[SignalReview, ...],
    *,
    catalog_path: Path,
    bar_type_suffix: str,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """用同一 Catalog 计算信号后的第 N 个可用交易日收益。"""
    if not reviews:
        return pd.DataFrame(columns=SIGNAL_COLUMNS)
    catalog = CatalogRepository(catalog_path)
    close_cache: dict[str, pd.Series[float]] = {}
    records: list[dict[str, object]] = []
    for review in reviews:
        closes = close_cache.get(review.instrument_id)
        if closes is None and review.instrument_id != "CASH.USD":
            bars = catalog.read_bars(BarType.from_str(f"{review.instrument_id}-{bar_type_suffix}"))
            closes = pd.Series(
                [bar.close.as_double() for bar in bars],
                index=pd.DatetimeIndex(
                    [pd.Timestamp(bar.ts_init, unit="ns", tz="UTC").normalize() for bar in bars]
                ),
                dtype="float64",
            )
            closes = closes[~closes.index.duplicated(keep="last")].sort_index()
            close_cache[review.instrument_id] = closes
        forward = _forward_returns(closes, review.timestamp_utc, horizons)
        record: dict[str, object] = {
            "timestamp_utc": review.timestamp_utc,
            "event_id": review.event_id,
            "strategy_name": review.strategy_name,
            "instrument_id": review.instrument_id,
            "direction": review.direction,
            "target_weight": review.target_weight,
            "status": review.status,
            "reason": review.reason,
            "risk_summary": review.risk_summary,
            "planned_orders": json.dumps(review.planned_orders, ensure_ascii=False),
        }
        record.update({f"forward_{horizon}_sessions": value for horizon, value in forward.items()})
        records.append(record)
    return pd.DataFrame.from_records(records, columns=SIGNAL_COLUMNS)


def _forward_returns(
    closes: pd.Series[float] | None,
    timestamp: object,
    horizons: tuple[int, ...],
) -> dict[int, float | None]:
    if closes is None or closes.empty:
        return dict.fromkeys(horizons)
    signal_session = pd.Timestamp(timestamp).tz_convert("UTC").normalize()
    base_index = int(closes.index.searchsorted(signal_session, side="right")) - 1
    if base_index < 0:
        return dict.fromkeys(horizons)
    base = float(closes.iloc[base_index])
    return {
        horizon: (
            float(closes.iloc[base_index + horizon]) / base - 1.0
            if base_index + horizon < len(closes)
            else None
        )
        for horizon in horizons
    }


def load_backtest_reports(report_root: Path) -> tuple[BacktestReportView, ...]:
    """只读加载回测生成的稳定 JSON/CSV 报告。"""
    if not report_root.exists():
        return ()
    reports: list[BacktestReportView] = []
    for directory in sorted(report_root.iterdir(), reverse=True):
        summary_path = directory / "summary.json"
        if not directory.is_dir() or not summary_path.is_file():
            continue
        raw_summary: object = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(raw_summary, dict):
            continue
        returns_path = directory / "returns.csv"
        fills_path = directory / "fills.csv"
        reports.append(
            BacktestReportView(
                run_id=directory.name,
                summary={str(key): value for key, value in raw_summary.items()},
                returns=pd.read_csv(returns_path) if returns_path.is_file() else pd.DataFrame(),
                fills=pd.read_csv(fills_path) if fills_path.is_file() else pd.DataFrame(),
            )
        )
    return tuple(reports)
