"""M4 Streamlit 只读运行看板。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from trading_assistant.dashboard.data import (
    AuditViews,
    BacktestReportView,
    load_audit_views,
    load_backtest_reports,
    mask_account_id,
    signal_review_frame,
)
from trading_assistant.storage.repository import SignalReview


@st.cache_data(ttl=10)
def _cached_audit(database_url: str, account_id: str, scope: str) -> AuditViews:
    return load_audit_views(database_url=database_url, account_id=account_id, scope=scope)


@st.cache_data(ttl=60)
def _cached_signals(
    audit_signals: tuple[SignalReview, ...], catalog_path: str, bar_type_suffix: str
) -> pd.DataFrame:
    return signal_review_frame(
        audit_signals,
        catalog_path=Path(catalog_path),
        bar_type_suffix=bar_type_suffix,
    )


@st.cache_data(ttl=60)
def _cached_reports(report_root: str) -> tuple[BacktestReportView, ...]:
    return load_backtest_reports(Path(report_root))


def _render_account(audit: AuditViews, *, stale_seconds: int) -> None:
    st.subheader("账户概览")
    snapshot = audit.portfolio
    if snapshot is None:
        st.info("尚无账户快照。TradingNode 连接 IBKR 后会自动写入。")
        return
    age_seconds = (datetime.now(UTC) - snapshot.timestamp_utc).total_seconds()
    if age_seconds > stale_seconds:
        st.warning(f"快照已超过 {stale_seconds} 秒未更新, 请检查 TradingNode/IBKR 连接。")
    columns = st.columns(4)
    columns[0].metric("账户", mask_account_id(snapshot.account_id))
    columns[1].metric("净清算值", f"{snapshot.net_liquidation:,.2f} {snapshot.currency}")
    columns[2].metric("可用资金", f"{snapshot.free_cash:,.2f} {snapshot.currency}")
    columns[3].metric("锁定资金", f"{snapshot.locked_cash:,.2f} {snapshot.currency}")
    st.caption(f"快照时间(UTC): {snapshot.timestamp_utc.isoformat()}")
    if not snapshot.positions:
        st.info("当前没有开仓仓位。")
        return
    st.dataframe(
        pd.DataFrame.from_records([position.__dict__ for position in snapshot.positions]),
        width="stretch",
        hide_index=True,
    )


def _render_signals(frame: pd.DataFrame) -> None:
    st.subheader("信号复盘")
    if frame.empty:
        st.info("尚无可复盘信号, 或 Catalog 中没有相应历史价格。")
        return
    status_options = sorted(str(value) for value in frame["status"].dropna().unique())
    selected = st.multiselect("工作流状态", status_options, default=status_options)
    focus_rejected = st.checkbox("只看否决、风控拒绝与过期")
    visible = frame[frame["status"].isin(selected)]
    if focus_rejected:
        visible = visible[visible["status"].isin(("DENIED", "RISK_REJECTED", "EXPIRED"))]
    st.caption("收益使用信号时点最近收盘价, 并从同一 M1 Catalog 取后续第 N 个交易日收盘价。")
    st.dataframe(
        visible,
        width="stretch",
        hide_index=True,
        column_config={
            "target_weight": st.column_config.NumberColumn(format="percent"),
            "forward_5_sessions": st.column_config.NumberColumn(format="percent"),
            "forward_10_sessions": st.column_config.NumberColumn(format="percent"),
            "forward_20_sessions": st.column_config.NumberColumn(format="percent"),
        },
    )


def _render_backtests(reports: tuple[BacktestReportView, ...]) -> None:
    st.subheader("回测报告")
    if not reports:
        st.info("reports/backtests 下尚无回测报告。")
        return
    by_id = {report.run_id: report for report in reports}
    selected = st.selectbox("回测运行", tuple(by_id))
    report = by_id[selected]
    st.json(report.summary)
    if not report.returns.empty:
        curve = report.returns.copy()
        if "timestamp_utc" in curve:
            curve["timestamp_utc"] = pd.to_datetime(curve["timestamp_utc"], utc=True)
            curve = curve.set_index("timestamp_utc")
        chart_columns = [name for name in ("equity", "cash") if name in curve]
        if chart_columns:
            st.line_chart(curve[chart_columns])
        st.dataframe(report.returns, width="stretch", hide_index=True)
    st.markdown("#### 成交")
    if report.fills.empty:
        st.info("该次回测没有成交。")
    else:
        st.dataframe(report.fills, width="stretch", hide_index=True)


def _render_risk_and_orders(audit: AuditViews) -> None:
    st.subheader("风控与订单")
    st.markdown("#### 风控及审批记录")
    if audit.decisions.empty:
        st.info("尚无风控或审批记录。")
    else:
        st.dataframe(audit.decisions, width="stretch", hide_index=True)
    st.markdown("#### 订单生命周期")
    if audit.orders.empty:
        st.info("尚无订单事件。")
    else:
        st.dataframe(audit.orders, width="stretch", hide_index=True)


def main() -> None:
    """读取本地审计数据并渲染四个只读页面。"""
    st.set_page_config(page_title="Trading Assistant", layout="wide")
    st.title("Trading Assistant")
    st.caption("只读运行看板 · 不包含审批、下单或撤单入口")

    database_url = os.environ.get("DATABASE_URL", "sqlite:///./data/trading_assistant.db")
    catalog_path = os.environ.get("CATALOG_PATH", "./catalog/eodhd")
    report_root = os.environ.get("REPORT_ROOT", "./reports/backtests")
    account = os.environ.get("TWS_ACCOUNT", "").strip()
    if not account:
        st.warning("未配置 TWS_ACCOUNT; 账户及该作用域的审计数据暂不显示。")
    account_id = f"IB-{account}"
    scope = f"paper:{account}"
    audit = _cached_audit(database_url, account_id, scope)
    signal_frame = (
        _cached_signals(tuple(audit.signals), catalog_path, "1-DAY-LAST-EXTERNAL")
        if Path(catalog_path).exists()
        else pd.DataFrame()
    )
    reports = _cached_reports(report_root)

    account_tab, signal_tab, backtest_tab, risk_tab = st.tabs(
        ("账户概览", "信号复盘", "回测报告", "风控与订单")
    )
    with account_tab:
        _render_account(
            audit,
            stale_seconds=int(os.environ.get("PORTFOLIO_SNAPSHOT_STALE_SECONDS", "90")),
        )
    with signal_tab:
        _render_signals(signal_frame)
    with backtest_tab:
        _render_backtests(reports)
    with risk_tab:
        _render_risk_and_orders(audit)


if __name__ == "__main__":
    main()
