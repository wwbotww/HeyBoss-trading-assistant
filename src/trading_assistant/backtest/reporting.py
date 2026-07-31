"""基于 NautilusTrader 账户状态的回测报告。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from math import sqrt
from pathlib import Path
from typing import Any

import pandas as pd

from trading_assistant.storage.repository import FillAudit


@dataclass(frozen=True)
class BacktestReport:
    """已落盘的回测报告摘要。"""

    summary: dict[str, Any]
    report_directory: Path


def load_nt_equity_curve(
    snapshot_path: Path,
    *,
    evaluation_start: date,
    end: date | None,
) -> pd.DataFrame:
    """读取 SimulationModule 输出并裁剪正式评估区间。"""
    loaded: object = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list) or not loaded:
        raise ValueError("NT 权益快照为空")
    records: list[dict[str, object]] = []
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise ValueError(f"NT 权益快照第 {index} 行结构无效")
        session_date = item.get("session_date")
        if not isinstance(session_date, str):
            raise ValueError(f"NT 权益快照第 {index} 行缺少 session_date")
        try:
            trading_day = date.fromisoformat(session_date)
            row = {
                "timestamp_utc": pd.Timestamp(trading_day, tz="UTC"),
                "equity": float(item["equity"]),
                "cash": float(item["cash"]),
                "market_value": float(item["market_value"]),
                "dividend_cashflow": float(item["dividend_cashflow"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"NT 权益快照第 {index} 行字段无效") from exc
        if trading_day < evaluation_start or (end is not None and trading_day > end):
            continue
        records.append(row)
    if not records:
        raise ValueError("正式评估区间内没有 NT 权益快照")
    curve = pd.DataFrame.from_records(records).set_index("timestamp_utc").sort_index()
    curve["daily_return"] = curve["equity"].pct_change().fillna(0.0)
    return curve


def calculate_summary(
    curve: pd.DataFrame,
    fills: tuple[FillAudit, ...],
    *,
    trading_days_per_year: int,
    risk_free_rate: float,
) -> dict[str, Any]:
    """只基于正式区间的 NT 权益状态计算绩效指标。"""
    if curve.empty:
        raise ValueError("权益曲线不能为空")
    initial_equity = float(curve["equity"].iloc[0])
    final_equity = float(curve["equity"].iloc[-1])
    final_cash = float(curve["cash"].iloc[-1])
    if initial_equity <= 0:
        raise ValueError("初始权益必须为正数")
    periods = max(len(curve) - 1, 1)
    annualized_return = (final_equity / initial_equity) ** (trading_days_per_year / periods) - 1.0
    drawdown = curve["equity"] / curve["equity"].cummax() - 1.0
    daily_returns = curve["daily_return"].iloc[1:]
    daily_rf = risk_free_rate / trading_days_per_year
    volatility = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = (
        (float(daily_returns.mean()) - daily_rf) / volatility * sqrt(trading_days_per_year)
        if volatility > 0
        else 0.0
    )
    traded_notional = sum(fill.quantity * fill.price for fill in fills)
    average_equity = float(curve["equity"].mean())
    turnover = traded_notional / average_equity if average_equity > 0 else 0.0
    return {
        "annualized_return": annualized_return,
        "max_drawdown": float(drawdown.min()),
        "sharpe_ratio": sharpe,
        "turnover": turnover,
        "fill_count": len(fills),
        "initial_equity_usd": initial_equity,
        "final_equity_usd": final_equity,
        "final_cash_usd": final_cash,
        "dividend_income_usd": float(curve["dividend_cashflow"].sum()),
        "total_commission_usd": sum(fill.commission for fill in fills),
        "start_date": str(curve.index[0].date()),
        "end_date": str(curve.index[-1].date()),
    }


def write_backtest_report(
    *,
    report_directory: Path,
    curve: pd.DataFrame,
    fills: tuple[FillAudit, ...],
    summary: dict[str, Any],
    orders: pd.DataFrame,
    positions: pd.DataFrame,
    account: pd.DataFrame,
) -> BacktestReport:
    """写出摘要、NT 原生状态报告与审计成交。"""
    report_directory.mkdir(parents=True, exist_ok=True)
    (report_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "trade_id": fill.trade_id,
                "timestamp_utc": fill.timestamp_utc.isoformat(),
                "instrument_id": fill.instrument_id,
                "client_order_id": fill.client_order_id,
                "direction": fill.direction,
                "quantity": fill.quantity,
                "price": fill.price,
                "commission": fill.commission,
            }
            for fill in fills
        ]
    ).to_csv(report_directory / "fills.csv", index=False)
    curve.reset_index().to_csv(report_directory / "returns.csv", index=False)
    orders.to_csv(report_directory / "orders.csv")
    positions.to_csv(report_directory / "positions.csv")
    account.to_csv(report_directory / "account.csv")
    return BacktestReport(summary=summary, report_directory=report_directory)
