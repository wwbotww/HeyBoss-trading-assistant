"""回测成交重放与结果报告。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Any

import pandas as pd
from nautilus_trader.model.data import BarType

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.storage.repository import FillAudit


@dataclass(frozen=True)
class BacktestReport:
    """已落盘的回测报告摘要。"""

    summary: dict[str, Any]
    report_directory: Path


def load_close_prices(
    catalog: CatalogRepository,
    *,
    bar_types: tuple[str, ...],
) -> pd.DataFrame:
    """从 M1 Catalog 构建按 UTC 交易日对齐的收盘价矩阵。"""
    records: list[dict[str, object]] = []
    for value in bar_types:
        bar_type = BarType.from_str(value)
        instrument_id = str(bar_type.instrument_id)
        records.extend(
            {
                "timestamp_utc": pd.Timestamp(bar.ts_init, unit="ns", tz="UTC").normalize(),
                "instrument_id": instrument_id,
                "close": bar.close.as_double(),
            }
            for bar in catalog.read_bars(bar_type)
        )
    if not records:
        raise ValueError("Catalog 中没有可用于回测报告的 Bar")
    frame = pd.DataFrame.from_records(records)
    closes = frame.pivot(index="timestamp_utc", columns="instrument_id", values="close")
    return closes.sort_index().ffill().dropna()


def build_equity_curve(
    closes: pd.DataFrame,
    fills: tuple[FillAudit, ...],
    *,
    starting_balance_usd: float,
) -> pd.DataFrame:
    """以 NT 成交为事实。按每日收盘价重放现金、持仓和权益。"""
    cash = starting_balance_usd
    quantities = {str(column): 0.0 for column in closes.columns}
    ordered_fills = sorted(fills, key=lambda fill: fill.timestamp_utc)
    fill_index = 0
    rows: list[dict[str, object]] = []

    for timestamp, prices in closes.iterrows():
        session_end = pd.Timestamp(timestamp).to_pydatetime()
        while fill_index < len(ordered_fills):
            fill = ordered_fills[fill_index]
            if fill.timestamp_utc.date() > session_end.date():
                break
            signed_notional = fill.quantity * fill.price
            if fill.direction == "BUY":
                cash -= signed_notional + fill.commission
                quantities[fill.instrument_id] = (
                    quantities.get(fill.instrument_id, 0.0) + fill.quantity
                )
            elif fill.direction == "SELL":
                cash += signed_notional - fill.commission
                quantities[fill.instrument_id] = (
                    quantities.get(fill.instrument_id, 0.0) - fill.quantity
                )
            else:
                raise ValueError(f"未知成交方向: {fill.direction}")
            fill_index += 1

        market_value = sum(
            quantity * float(prices[instrument_id])
            for instrument_id, quantity in quantities.items()
            if instrument_id in prices.index
        )
        rows.append(
            {
                "timestamp_utc": timestamp,
                "equity": cash + market_value,
                "cash": cash,
            }
        )

    curve = pd.DataFrame.from_records(rows).set_index("timestamp_utc")
    curve["daily_return"] = curve["equity"].pct_change().fillna(0.0)
    return curve


def calculate_summary(
    curve: pd.DataFrame,
    fills: tuple[FillAudit, ...],
    *,
    starting_balance_usd: float,
    trading_days_per_year: int,
    risk_free_rate: float,
) -> dict[str, Any]:
    """计算 M2 约定的绩效指标。"""
    if curve.empty:
        raise ValueError("权益曲线不能为空")
    final_equity = float(curve["equity"].iloc[-1])
    final_cash = float(curve["cash"].iloc[-1])
    periods = max(len(curve) - 1, 1)
    annualized_return = (final_equity / starting_balance_usd) ** (
        trading_days_per_year / periods
    ) - 1.0
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
        "final_equity_usd": final_equity,
        "final_cash_usd": final_cash,
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
) -> BacktestReport:
    """以稳定文件名写入 JSON 与 CSV 报告。"""
    report_directory.mkdir(parents=True, exist_ok=False)
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
    return BacktestReport(summary=summary, report_directory=report_directory)
