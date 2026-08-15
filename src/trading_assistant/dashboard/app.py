"""Streamlit 只读运行看板。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from trading_assistant.dashboard.data import (
    AuditViews,
    BacktestReportView,
    DataQualityReportView,
    FactorSnapshotView,
    load_audit_views,
    load_backtest_reports,
    load_catalog_overview,
    load_factor_snapshot,
    load_latest_data_quality,
    mask_account_id,
    signal_review_frame,
    workflow_summary_frame,
    workflow_timeline,
)
from trading_assistant.storage.repository import SignalReview

PAGES = ("总览", "Paper 交易", "策略与信号", "回测", "数据与系统")
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
PAGE_DESCRIPTIONS = {
    "总览": "先判断数据、因子、账户快照和工作流是否需要关注。",
    "Paper 交易": "追踪账户、资金、工作流、订单与成交的只读审计事实。",
    "策略与信号": "解释最新因子横截面、目标组合和信号事后表现。",
    "回测": "检查正式评估边界、绩效条件和 NautilusTrader 运行明细。",
    "数据与系统": "核对行情覆盖、数据质量、因子交付和运行依赖顺序。",
}
POSITIVE_STATUSES = {
    "APPROVED",
    "COMPLETED",
    "FILLED",
    "ORDERS_SUBMITTED",
    "REARMED",
    "SUBMITTED",
}
WAITING_STATUSES = {"CREATED", "GENERATED", "NEW", "PENDING", "PROCESSING", "RUNNING"}
FAILED_STATUSES = {"DENIED", "ERROR", "FAILED", "REJECTED", "RISK_REJECTED"}
BACKTEST_COLUMN_LABELS = {
    "timestamp_utc": "时间(UTC)",
    "trade_id": "成交ID",
    "client_order_id": "订单ID",
    "trader_id": "交易员ID",
    "strategy_id": "策略ID",
    "instrument_id": "标的",
    "venue_order_id": "场内订单ID",
    "position_id": "持仓ID",
    "account_id": "账户",
    "last_trade_id": "最新成交ID",
    "type": "类型",
    "side": "方向",
    "direction": "方向",
    "quantity": "数量",
    "time_in_force": "有效期",
    "filled_qty": "已成交数量",
    "avg_px": "平均成交价",
    "price": "价格",
    "slippage": "滑点",
    "commission": "佣金",
    "commissions": "佣金",
    "status": "状态",
    "ts_init": "初始化时间(ns)",
    "ts_last": "最后更新时间(ns)",
    "signed_quantity": "持仓数量",
    "avg_open_price": "平均开仓价",
    "realized_pnl": "已实现盈亏",
    "total": "总额",
    "locked": "锁定资金",
    "free": "可用资金",
    "currency": "币种",
    "account_type": "账户类型",
    "base_currency": "基础币种",
    "equity": "权益",
    "cash": "现金",
    "market_value": "市场价值",
    "dividend_cashflow": "分红现金流",
    "daily_return": "日收益",
}
DASHBOARD_CSS = """
<style>
[data-testid="stAppViewContainer"] .block-container {
    max-width: 1480px;
    padding-top: 2rem;
    padding-bottom: 4rem;
}
[data-testid="stSidebar"] {
    border-right: 1px solid #233243;
}
[data-testid="stMetric"] {
    min-height: 7.2rem;
    padding: 1rem 1.05rem;
    border: 1px solid #233243;
    border-radius: 0.75rem;
    background: linear-gradient(145deg, rgba(23, 38, 52, 0.96), rgba(14, 27, 39, 0.96));
}
[data-testid="stMetricLabel"] {
    color: #9fb0c0;
}
[data-testid="stMetricValue"] {
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
}
[data-testid="stAlert"], [data-testid="stExpander"], [data-testid="stDataFrame"] {
    border-radius: 0.75rem;
}
@media (max-width: 768px) {
    [data-testid="stAppViewContainer"] .block-container {
        padding-top: 1.25rem;
        padding-left: 1rem;
        padding-right: 1rem;
    }
    [data-testid="stMetric"] {
        min-height: 6.25rem;
        padding: 0.8rem;
    }
}
</style>
"""


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


@st.cache_data(ttl=60)
def _cached_factor(catalog_path: str) -> FactorSnapshotView | None:
    return load_factor_snapshot(Path(catalog_path))


@st.cache_data(ttl=300)
def _cached_catalog_overview(catalog_path: str) -> pd.DataFrame:
    return load_catalog_overview(Path(catalog_path))


@st.cache_data(ttl=60)
def _cached_data_quality(report_root: str) -> DataQualityReportView | None:
    return load_latest_data_quality(Path(report_root))


def _beijing_time(value: object) -> str:
    """把 UTC 审计时间格式化为北京时间。"""
    if value is None or bool(pd.isna(value)):
        return "—"
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return str(timestamp.tz_convert(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"))


def _short_id(value: object, *, length: int = 8) -> str:
    text = str(value)
    return text if len(text) <= length else f"{text[:length]}…"


def _with_beijing_time(
    frame: pd.DataFrame,
    *,
    source: str = "timestamp_utc",
    destination: str = "时间(北京)",
) -> pd.DataFrame:
    result = frame.copy()
    if source not in result:
        return result
    result.insert(0, destination, result[source].map(_beijing_time))
    return result.drop(columns=[source])


def _render_page_header(page: str) -> None:
    """渲染稳定的页面标题与职责说明。"""
    st.header(page)
    st.caption(PAGE_DESCRIPTIONS[page])


def _status_display(value: object) -> str:
    """为审计状态增加不依赖颜色的语义说明。"""
    status = str(value)
    if status in POSITIVE_STATUSES:
        return f"🟢 正常 · {status}"
    if status in WAITING_STATUSES:
        return f"🟡 等待 · {status}"
    if status in FAILED_STATUSES:
        return f"🔴 异常 · {status}"
    if status == "EXPIRED":
        return "⚪ 已结束 · EXPIRED"
    return f"记录 · {status}"


def _backtest_detail_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """整理回测明细的展示列, 并确保账户标识始终脱敏。"""
    display = frame.copy()
    unnamed = [name for name in display if str(name).startswith("Unnamed:")]
    if unnamed:
        display = display.drop(columns=unnamed)
    if "account_id" in display:
        display["account_id"] = display["account_id"].astype(str).map(mask_account_id)
    if "status" in display:
        display["status"] = display["status"].map(_status_display)
    display = display.dropna(axis="columns", how="all")
    return display.rename(columns=BACKTEST_COLUMN_LABELS)


def _render_account(audit: AuditViews, *, stale_seconds: int) -> None:
    st.subheader("账户概览")
    snapshot = audit.portfolio
    if snapshot is None:
        st.info("尚无账户快照。TradingNode 连接 IBKR 后会自动写入。")
        return
    age_seconds = (datetime.now(UTC) - snapshot.timestamp_utc).total_seconds()
    if age_seconds > stale_seconds:
        st.warning(
            f"账户快照已超过 {stale_seconds} 秒未更新; "
            "这里只表示快照陈旧, 不能判断 IBKR 当前连接状态。"
        )
    columns = st.columns(4)
    columns[0].metric("账户", mask_account_id(snapshot.account_id))
    columns[1].metric("净清算值", f"{snapshot.net_liquidation:,.2f} {snapshot.currency}")
    columns[2].metric("可用资金", f"{snapshot.free_cash:,.2f} {snapshot.currency}")
    columns[3].metric("锁定资金", f"{snapshot.locked_cash:,.2f} {snapshot.currency}")
    st.caption(
        f"快照时间: {_beijing_time(snapshot.timestamp_utc)} 北京 / "
        f"{snapshot.timestamp_utc.isoformat()} UTC"
    )
    if not snapshot.positions:
        st.info("当前没有开仓仓位。")
        return
    positions = pd.DataFrame.from_records(
        [position.__dict__ for position in snapshot.positions]
    ).rename(
        columns={
            "instrument_id": "标的",
            "signed_quantity": "持仓数量",
            "side": "方向",
            "avg_open_price": "平均开仓价",
            "realized_pnl": "已实现盈亏",
        }
    )
    st.dataframe(
        positions,
        width="stretch",
        hide_index=True,
    )


def _render_signals(frame: pd.DataFrame) -> None:
    st.markdown("### 组合级信号复盘")
    if frame.empty:
        st.info("尚无已落库信号。")
        return
    status_options = sorted(str(value) for value in frame["status"].dropna().unique())
    selected = st.multiselect(
        "信号状态",
        status_options,
        default=status_options,
        key="signal_status_filter",
    )
    focus_rejected = st.checkbox(
        "只看否决、风控拒绝与过期",
        key="signal_rejected_filter",
    )
    visible = frame[frame["status"].isin(selected)]
    if focus_rejected:
        visible = visible[visible["status"].isin(("DENIED", "RISK_REJECTED", "EXPIRED"))]
    if visible.empty:
        st.info("当前筛选条件下没有信号。")
        return
    event_ids = tuple(str(value) for value in visible["event_id"].drop_duplicates())
    selected_event = st.selectbox(
        "信号事件",
        event_ids,
        format_func=lambda value: _short_id(value),
    )
    if selected_event not in event_ids:
        selected_event = event_ids[0]
    event_frame = visible[visible["event_id"] == selected_event].copy()
    first = event_frame.iloc[0]
    metrics = st.columns(3)
    metrics[0].metric("策略", str(first["strategy_name"]))
    metrics[1].metric("状态", _status_display(first["status"]))
    metrics[2].metric("目标标的", len(event_frame))
    st.caption("收益使用信号时点最近收盘价, 并从同一 Catalog 取后续第 N 个交易日收盘价。")
    display = _with_beijing_time(event_frame)
    display = display[
        [
            "时间(北京)",
            "instrument_id",
            "direction",
            "target_weight",
            "forward_5_sessions",
            "forward_10_sessions",
            "forward_20_sessions",
        ]
    ].rename(
        columns={
            "instrument_id": "标的",
            "direction": "方向",
            "target_weight": "目标权重",
            "forward_5_sessions": "后5交易日",
            "forward_10_sessions": "后10交易日",
            "forward_20_sessions": "后20交易日",
        }
    )
    st.dataframe(
        display,
        width="stretch",
        hide_index=True,
        column_config={
            "目标权重": st.column_config.NumberColumn(format="percent"),
            "后5交易日": st.column_config.NumberColumn(format="percent"),
            "后10交易日": st.column_config.NumberColumn(format="percent"),
            "后20交易日": st.column_config.NumberColumn(format="percent"),
        },
    )
    with st.expander("信号依据与技术详情"):
        st.write(str(first["reason"]))
        if first["risk_summary"]:
            st.warning(str(first["risk_summary"]))
        st.code(str(selected_event))
        st.caption(f"原始 UTC: {pd.Timestamp(first['timestamp_utc']).isoformat()}")


def _render_backtests(reports: tuple[BacktestReportView, ...]) -> None:
    _render_page_header("回测")
    if not reports:
        st.info("reports/backtests 下尚无回测报告。")
        return
    by_id = {report.run_id: report for report in reports}
    comparison_ids = st.multiselect(
        "对比运行 (最多 3 个)",
        tuple(by_id),
        default=tuple(by_id)[:3],
        max_selections=3,
        key="backtest_comparison",
    )
    comparison = pd.DataFrame.from_records(
        [
            {
                "运行": report.run_id,
                "状态": "🔴 异常 · 损坏" if report.load_error else "🟢 正常 · 可读",
                "策略": report.summary.get("strategy_name", "—"),
                "数据起点": report.summary.get("data_start", "—"),
                "评估起点": report.summary.get("evaluation_start", "—"),
                "评估终点": report.summary.get("end", report.summary.get("end_date", "—")),
                "年化收益": report.summary.get("annualized_return"),
                "最大回撤": report.summary.get("max_drawdown"),
                "夏普": report.summary.get("sharpe_ratio"),
                "换手": report.summary.get("turnover"),
                "成交": report.summary.get("fill_count"),
                "佣金(USD)": report.summary.get("total_commission_usd"),
            }
            for report_id in comparison_ids
            if (report := by_id.get(str(report_id))) is not None
        ]
    )
    if not comparison.empty:
        st.dataframe(
            comparison,
            width="stretch",
            hide_index=True,
            column_config={
                "年化收益": st.column_config.NumberColumn(format="percent"),
                "最大回撤": st.column_config.NumberColumn(format="percent"),
                "换手": st.column_config.NumberColumn(format="percent"),
                "佣金(USD)": st.column_config.NumberColumn(format="%.2f"),
            },
        )

    selected = st.selectbox("查看运行详情", tuple(by_id))
    if selected not in by_id:
        selected = next(iter(by_id))
    report = by_id[selected]
    if report.load_error is not None:
        st.error(f"运行 {report.run_id} 无法读取: {report.load_error}")
        return

    summary = report.summary
    short_evaluation = (
        len(report.returns) <= 1
        or summary.get("evaluation_start") == summary.get("end")
        or summary.get("start_date") == summary.get("end_date")
    )
    if short_evaluation:
        st.warning("该报告仅用于链路验证, 不适合作为绩效结论。")

    metric_values = (
        ("年化收益", summary.get("annualized_return"), "percent"),
        ("最大回撤", summary.get("max_drawdown"), "percent"),
        ("夏普比率", summary.get("sharpe_ratio"), "number"),
        ("换手率", summary.get("turnover"), "percent"),
        ("成交笔数", summary.get("fill_count", len(report.fills)), "integer"),
        ("累计佣金", summary.get("total_commission_usd"), "money"),
        ("期初权益", summary.get("initial_equity_usd"), "money"),
        ("期末权益", summary.get("final_equity_usd"), "money"),
    )
    for index, (label, raw_value, kind) in enumerate(metric_values):
        if index % 4 == 0:
            metric_columns = st.columns(4)
        if isinstance(raw_value, int | float):
            if kind == "percent":
                value = f"{float(raw_value):.2%}"
            elif kind == "integer":
                value = f"{int(raw_value):,}"
            elif kind == "money":
                value = f"{float(raw_value):,.2f} USD"
            else:
                value = f"{float(raw_value):.3f}"
        else:
            value = "—"
        metric_columns[index % 4].metric(label, value)

    st.caption(
        "数据预热: "
        f"{summary.get('data_start', '—')} · 正式评估: "
        f"{summary.get('evaluation_start', summary.get('start_date', '—'))} → "
        f"{summary.get('end', summary.get('end_date', '—'))} · "
        f"信号 Bar: {summary.get('signal_bar_type_suffix', '—')} · "
        f"执行 Bar: {summary.get('execution_bar_type_suffix', '—')}"
    )

    if not report.returns.empty:
        curve = report.returns.copy()
        if "timestamp_utc" in curve:
            curve["timestamp_utc"] = pd.to_datetime(curve["timestamp_utc"], utc=True)
            curve = curve.set_index("timestamp_utc")
        st.markdown("### 权益与回撤")
        if len(curve) <= 1:
            st.info("正式评估区间只有一个估值点, 尚不能形成权益、回撤或资金构成曲线。")
        else:
            if "equity" in curve:
                st.line_chart(curve[["equity"]], y_label="权益 (USD)")
                drawdown = curve["equity"] / curve["equity"].cummax() - 1.0
                st.area_chart(drawdown.rename("drawdown"), y_label="回撤")
            capital_columns = [name for name in ("cash", "market_value") if name in curve]
            if capital_columns:
                st.markdown("### 资金构成")
                st.area_chart(curve[capital_columns], y_label="USD")
    else:
        st.info("该次回测没有权益曲线。")

    st.markdown("### 运行明细")
    details = (
        ("订单", report.orders),
        ("成交", report.fills),
        ("持仓", report.positions),
        ("账户", report.account),
        ("逐日权益", report.returns),
    )
    for label, frame in details:
        with st.expander(f"{label} ({len(frame)})"):
            if frame.empty:
                st.info(f"该次回测没有{label}记录。")
            else:
                st.dataframe(
                    _backtest_detail_frame(frame),
                    width="stretch",
                    hide_index=True,
                )
    with st.expander("原始摘要"):
        st.json(summary)


def _render_overview(
    audit: AuditViews,
    factor: FactorSnapshotView | None,
    *,
    stale_seconds: int,
) -> None:
    """渲染当前运行状态、账户摘要和需要关注的工作流。"""
    _render_page_header("总览")
    latest_workflow = audit.workflows[0] if audit.workflows else None
    snapshot = audit.portfolio
    action_statuses = {"PENDING", "PROCESSING", "RISK_REJECTED", "DENIED"}
    action_count = sum(workflow.status in action_statuses for workflow in audit.workflows)
    metrics = st.columns(4)
    metrics[0].metric(
        "最近观测策略",
        latest_workflow.strategy_name if latest_workflow is not None else "无记录",
    )
    metrics[1].metric("最新因子日期", factor.asof_date if factor is not None else "无数据")
    metrics[2].metric(
        "账户快照",
        _beijing_time(snapshot.timestamp_utc) if snapshot is not None else "无快照",
    )
    metrics[3].metric("需关注工作流", action_count)

    if snapshot is None:
        st.info("尚无账户快照; 这不代表 IBKR 当前离线。")
    else:
        age_seconds = (datetime.now(UTC) - snapshot.timestamp_utc).total_seconds()
        if age_seconds > stale_seconds:
            st.warning(
                f"账户快照已超过 {stale_seconds} 秒未更新; "
                "这里只表示快照陈旧, 不能判断 IBKR 当前连接状态。"
            )
        account_metrics = st.columns(4)
        account_metrics[0].metric(
            "净清算值", f"{snapshot.net_liquidation:,.2f} {snapshot.currency}"
        )
        account_metrics[1].metric("可用资金", f"{snapshot.free_cash:,.2f} {snapshot.currency}")
        account_metrics[2].metric("锁定资金", f"{snapshot.locked_cash:,.2f} {snapshot.currency}")
        account_metrics[3].metric("当前仓位", len(snapshot.positions))

    if factor is None:
        st.warning("Catalog 中尚无完整因子横截面。")
    elif factor.source_kind != "signal_inference":
        st.error(f"最新因子来源为 {factor.source_kind}, 不属于 paper 推理批次。")

    rejected = sum(workflow.status in {"RISK_REJECTED", "DENIED"} for workflow in audit.workflows)
    expired = sum(workflow.status == "EXPIRED" for workflow in audit.workflows)
    if rejected:
        st.error(f"有 {rejected} 个工作流被风控或人工否决。")
    if expired:
        st.info(f"有 {expired} 个工作流已过期结束。")

    st.markdown("### 最新工作流")
    workflow_frame = workflow_summary_frame(audit.workflows[:5])
    if workflow_frame.empty:
        st.info("尚无策略工作流。")
        return
    workflow_frame["event_id"] = workflow_frame["event_id"].map(_short_id)
    workflow_frame = _with_beijing_time(
        workflow_frame,
        source="signal_timestamp_utc",
        destination="信号时间(北京)",
    )
    workflow_frame = _with_beijing_time(
        workflow_frame,
        source="expires_at_utc",
        destination="到期时间(北京)",
    )
    workflow_frame["status"] = workflow_frame["status"].map(_status_display)
    st.dataframe(
        workflow_frame[
            [
                "信号时间(北京)",
                "strategy_name",
                "status",
                "target_count",
                "planned_order_count",
                "event_id",
            ]
        ].rename(
            columns={
                "strategy_name": "策略",
                "status": "状态",
                "target_count": "目标数",
                "planned_order_count": "计划订单数",
                "event_id": "事件ID",
            }
        ),
        width="stretch",
        hide_index=True,
    )


def _render_paper_trading(audit: AuditViews, *, stale_seconds: int) -> None:
    """渲染 Paper 账户、工作流和关联审计时间线。"""
    _render_page_header("Paper 交易")
    _render_account(audit, stale_seconds=stale_seconds)
    st.markdown("### 资金历史")
    if audit.account_history.empty:
        st.info("尚无账户资金历史。")
    else:
        history = audit.account_history.sort_values("timestamp_utc").copy()
        history["timestamp_utc"] = pd.to_datetime(history["timestamp_utc"], utc=True)
        history["timestamp_utc"] = (
            history["timestamp_utc"].dt.tz_convert(DISPLAY_TIMEZONE).dt.tz_localize(None)
        )
        history = history.rename(
            columns={
                "timestamp_utc": "时间(北京)",
                "net_liquidation": "净清算值",
                "free_cash": "可用资金",
                "locked_cash": "锁定资金",
            }
        )
        history_chart = history.melt(
            id_vars="时间(北京)",
            value_vars=("净清算值", "可用资金", "锁定资金"),
            var_name="资金类型",
            value_name="金额",
        )
        st.vega_lite_chart(
            history_chart,
            {
                "mark": {"type": "line", "point": True},
                "encoding": {
                    "x": {"field": "时间(北京)", "type": "temporal", "title": "时间(北京)"},
                    "y": {
                        "field": "金额",
                        "type": "quantitative",
                        "title": "USD",
                        "scale": {"zero": False},
                    },
                    "color": {
                        "field": "资金类型",
                        "type": "nominal",
                        "legend": {"title": None, "orient": "top"},
                    },
                    "tooltip": [
                        {"field": "时间(北京)", "type": "temporal", "title": "时间"},
                        {"field": "资金类型", "type": "nominal", "title": "类型"},
                        {
                            "field": "金额",
                            "type": "quantitative",
                            "title": "金额",
                            "format": ",.2f",
                        },
                    ],
                },
                "height": 260,
            },
            width="stretch",
        )

    st.markdown("### 工作流与交易审计")
    if not audit.workflows:
        st.info("尚无策略工作流。")
        return
    status_options = sorted({workflow.status for workflow in audit.workflows})
    selected_statuses = st.multiselect(
        "工作流状态",
        status_options,
        default=status_options,
        key="paper_workflow_status_filter",
    )
    visible_workflows = tuple(
        workflow for workflow in audit.workflows if workflow.status in selected_statuses
    )
    if not visible_workflows:
        st.info("当前筛选条件下没有工作流。")
        return
    summary = workflow_summary_frame(visible_workflows)
    summary["event_id"] = summary["event_id"].map(_short_id)
    summary = _with_beijing_time(
        summary,
        source="signal_timestamp_utc",
        destination="信号时间(北京)",
    )
    summary["status"] = summary["status"].map(_status_display)
    st.dataframe(
        summary[
            [
                "信号时间(北京)",
                "strategy_name",
                "status",
                "target_count",
                "planned_order_count",
                "event_id",
            ]
        ].rename(
            columns={
                "strategy_name": "策略",
                "status": "状态",
                "target_count": "目标数",
                "planned_order_count": "计划订单数",
                "event_id": "事件ID",
            }
        ),
        width="stretch",
        hide_index=True,
    )
    workflow_ids = tuple(workflow.event_id for workflow in visible_workflows)
    selected_event = st.selectbox(
        "查看工作流",
        workflow_ids,
        format_func=lambda value: _short_id(value),
    )
    if selected_event not in workflow_ids:
        selected_event = workflow_ids[0]
    workflow = next(value for value in visible_workflows if value.event_id == selected_event)

    order_count = (
        int((audit.orders["event_id"] == workflow.event_id).sum())
        if "event_id" in audit.orders
        else 0
    )
    fill_count = (
        int((audit.fills["event_id"] == workflow.event_id).sum())
        if "event_id" in audit.fills
        else 0
    )
    details = st.columns(4)
    details[0].metric("状态", _status_display(workflow.status))
    details[1].metric("目标标的", len(workflow.target_weights))
    details[2].metric("订单事件", order_count)
    details[3].metric("成交", fill_count)
    st.caption(
        f"策略: {workflow.strategy_name} · "
        f"信号: {_beijing_time(workflow.signal_timestamp_utc)} 北京 / "
        f"{workflow.signal_timestamp_utc.isoformat()} UTC · "
        f"到期: {_beijing_time(workflow.expires_at_utc)} 北京 / "
        f"{workflow.expires_at_utc.isoformat()} UTC"
    )
    if workflow.target_weights:
        st.dataframe(
            pd.DataFrame(workflow.target_weights, columns=("标的", "目标权重")),
            width="stretch",
            hide_index=True,
            column_config={"目标权重": st.column_config.NumberColumn(format="percent")},
        )
    if workflow.risk_summary:
        st.warning(workflow.risk_summary)
    if not workflow.planned_orders:
        st.info("该工作流没有计划订单。")
    else:
        st.dataframe(
            pd.DataFrame.from_records(workflow.planned_orders),
            width="stretch",
            hide_index=True,
        )

    timeline = workflow_timeline(audit, event_id=workflow.event_id)
    st.markdown("#### 关联时间线")
    if timeline.empty:
        st.info("该工作流没有可关联的审计事件。")
    else:
        timeline["event_id"] = timeline["event_id"].map(_short_id)
        timeline["client_order_id"] = timeline["client_order_id"].map(_short_id)
        timeline["status"] = timeline["status"].map(_status_display)
        timeline = _with_beijing_time(timeline)
        st.dataframe(
            timeline.rename(
                columns={
                    "event_type": "类型",
                    "status": "状态",
                    "instrument_id": "标的",
                    "direction": "方向",
                    "quantity": "数量",
                    "price": "价格",
                    "detail": "说明",
                    "event_id": "事件ID",
                    "client_order_id": "订单ID",
                }
            ),
            width="stretch",
            hide_index=True,
        )
    with st.expander("工作流技术详情"):
        st.code(workflow.event_id)
        st.write(workflow.reason)
        st.caption(f"调仓键: {workflow.rebalance_key}")


def _render_strategy_and_signals(
    audit: AuditViews,
    signal_frame: pd.DataFrame,
    factor: FactorSnapshotView | None,
) -> None:
    """渲染最新因子横截面、目标组合与组合级信号。"""
    _render_page_header("策略与信号")
    latest_workflow = audit.workflows[0] if audit.workflows else None
    header = st.columns(3)
    header[0].metric(
        "最近观测策略",
        latest_workflow.strategy_name if latest_workflow is not None else "无记录",
    )
    header[1].metric("工作流数量", len(audit.workflows))
    header[2].metric("逐标的信号数", len(audit.signals))

    st.markdown("### 最新 PatchTST 因子横截面")
    if factor is None:
        st.info("Catalog 中尚无完整 FactorScoreData 横截面。")
    else:
        matched_workflow = next(
            (
                workflow
                for workflow in audit.workflows
                if workflow.rebalance_key == f"{factor.model_release_id}:{factor.asof_date}"
            ),
            None,
        )
        target_weights = (
            dict(matched_workflow.target_weights) if matched_workflow is not None else {}
        )
        scores = factor.scores.copy()
        scores["selected"] = scores["canonical_id"].isin(target_weights)
        scores["target_weight"] = scores["canonical_id"].map(target_weights)
        eligible_count = int(scores["eligible"].sum())
        factor_metrics = st.columns(4)
        factor_metrics[0].metric("As-of", factor.asof_date)
        factor_metrics[1].metric("批次行数", factor.batch_size)
        factor_metrics[2].metric("Eligible", eligible_count)
        factor_metrics[3].metric("已落库目标", len(target_weights))

        eligible = scores[scores["eligible"]].copy()
        if not eligible.empty:
            eligible["组合状态"] = eligible["selected"].map({True: "目标组合", False: "候选"})
            st.vega_lite_chart(
                eligible,
                {
                    "mark": {"type": "bar", "cornerRadiusEnd": 4},
                    "encoding": {
                        "y": {
                            "field": "canonical_id",
                            "type": "nominal",
                            "sort": {"field": "rank", "order": "ascending"},
                            "title": "标的",
                        },
                        "x": {
                            "field": "score",
                            "type": "quantitative",
                            "title": "因子分数",
                            "scale": {"zero": True},
                            "stack": None,
                        },
                        "color": {
                            "field": "组合状态",
                            "type": "nominal",
                            "scale": {
                                "domain": ["目标组合", "候选"],
                                "range": ["#2DD4BF", "#526579"],
                            },
                            "legend": {"title": None, "orient": "top"},
                        },
                        "tooltip": [
                            {"field": "canonical_id", "type": "nominal", "title": "标的"},
                            {
                                "field": "score",
                                "type": "quantitative",
                                "title": "因子分数",
                                "format": ".4f",
                            },
                            {"field": "组合状态", "type": "nominal", "title": "组合状态"},
                        ],
                    },
                    "height": 320,
                },
                width="stretch",
            )
        display_scores = scores[
            ["rank", "canonical_id", "score", "eligible", "selected", "target_weight"]
        ].rename(
            columns={
                "rank": "排名",
                "canonical_id": "标的",
                "score": "因子分数",
                "eligible": "可入选",
                "selected": "目标组合",
                "target_weight": "目标权重",
            }
        )
        st.dataframe(
            display_scores,
            width="stretch",
            hide_index=True,
            column_config={"目标权重": st.column_config.NumberColumn(format="percent")},
        )
        st.caption(
            f"因子可用时间: {_beijing_time(factor.available_at_utc)} 北京 / "
            f"{factor.available_at_utc.isoformat()} UTC"
        )
        with st.expander("因子交付详情"):
            st.code(f"batch_id={factor.batch_id}")
            st.code(f"delivery_id={factor.delivery_id}")
            st.code(f"model_release_id={factor.model_release_id}")
            st.caption(f"source_kind={factor.source_kind}")

    _render_signals(signal_frame)


def _render_data_and_system(
    catalog_overview: pd.DataFrame,
    factor: FactorSnapshotView | None,
    quality: DataQualityReportView | None,
) -> None:
    """渲染 Catalog 覆盖、最新因子交付与数据质量状态。"""
    _render_page_header("数据与系统")
    st.caption("以下内容均来自本地只读资产, 不会触发同步、推理或交易。")

    st.markdown("### 最新数据质量报告")
    if quality is None:
        st.info("尚无数据质量报告。")
    elif quality.load_error is not None:
        st.error(quality.load_error)
    else:
        quality_metrics = st.columns(4)
        quality_metrics[0].metric("任务模式", quality.mode or "—")
        quality_metrics[1].metric("拉取 Bar", quality.bars_fetched or 0)
        quality_metrics[2].metric("写入 Bar", quality.bars_written or 0)
        quality_metrics[3].metric("错误 / 警告", f"{quality.error_count} / {quality.warning_count}")
        generated = (
            _beijing_time(quality.generated_at_utc)
            if quality.generated_at_utc is not None
            else "未知"
        )
        st.caption(
            f"生成时间: {generated} 北京 · 检查序列: {quality.report_entry_count} · "
            f"公司行动写入: {quality.corporate_actions_written or 0}"
        )
        if quality.error_count:
            st.error(f"最新任务包含 {quality.error_count} 个错误, 请先检查再使用数据。")
        elif quality.warning_count:
            st.warning(
                f"最新任务没有阻断错误, 但有 {quality.warning_count} 个警告; "
                "交易日候选缺口目前按工作日历检查, 需结合真实交易所日历理解。"
            )
        else:
            st.success("最新任务未报告数据质量问题。")
        if not quality.issue_counts.empty:
            issue_counts = quality.issue_counts.copy()
            issue_counts["severity"] = issue_counts["severity"].map(
                {"error": "错误", "warning": "警告"}
            )
            st.dataframe(
                issue_counts.rename(
                    columns={"severity": "级别", "code": "问题代码", "count": "数量"}
                ),
                width="stretch",
                hide_index=True,
            )
        with st.expander("按标的问题分布与明细"):
            if not quality.instrument_issue_counts.empty:
                instrument_counts = quality.instrument_issue_counts.copy()
                instrument_counts["severity"] = instrument_counts["severity"].map(
                    {"error": "错误", "warning": "警告"}
                )
                st.dataframe(
                    instrument_counts.rename(
                        columns={"severity": "级别", "instrument_id": "标的", "count": "数量"}
                    ),
                    width="stretch",
                    hide_index=True,
                )
            if not quality.issues.empty:
                issue_details = quality.issues.drop(columns=["message"], errors="ignore")
                issue_details["severity"] = issue_details["severity"].map(
                    {"error": "错误", "warning": "警告"}
                )
                issue_details = _with_beijing_time(issue_details)
                st.dataframe(
                    issue_details.rename(
                        columns={
                            "severity": "级别",
                            "code": "问题代码",
                            "instrument_id": "标的",
                        }
                    ),
                    width="stretch",
                    hide_index=True,
                )
                st.caption("原始错误消息不在看板展示, 避免泄露供应商请求上下文。")

    st.markdown("### NautilusTrader Catalog 覆盖")
    if catalog_overview.empty:
        st.info("Catalog 中尚无 Bar 数据。")
    else:
        source_values = catalog_overview["source"].astype(str)
        catalog_metrics = st.columns(4)
        catalog_metrics[0].metric("标的", catalog_overview["instrument_id"].nunique())
        catalog_metrics[1].metric("Bar 序列", len(catalog_overview))
        catalog_metrics[2].metric("INTERNAL", int((source_values == "INTERNAL").sum()))
        catalog_metrics[3].metric("EXTERNAL", int((source_values == "EXTERNAL").sum()))
        display = catalog_overview.copy()
        display["first_timestamp_utc"] = display["first_timestamp_utc"].map(
            lambda value: pd.Timestamp(value).date().isoformat()
        )
        display["last_timestamp_utc"] = display["last_timestamp_utc"].map(
            lambda value: pd.Timestamp(value).date().isoformat()
        )
        st.dataframe(
            display.rename(
                columns={
                    "bar_type": "BarType",
                    "instrument_id": "标的",
                    "source": "来源",
                    "bar_count": "Bar 数",
                    "first_timestamp_utc": "起始日期",
                    "last_timestamp_utc": "结束日期",
                }
            ),
            width="stretch",
            hide_index=True,
        )

    st.markdown("### 最新因子交付")
    if factor is None:
        st.info("Catalog 中尚无完整 FactorScoreData 横截面。")
    else:
        factor_metrics = st.columns(4)
        factor_metrics[0].metric("As-of", factor.asof_date)
        factor_metrics[1].metric("可用时间(北京)", _beijing_time(factor.available_at_utc))
        factor_metrics[2].metric("批次行数", factor.batch_size)
        factor_metrics[3].metric("Eligible", int(factor.scores["eligible"].sum()))
        st.caption(
            f"模型发布: {_short_id(factor.model_release_id, length=16)} · "
            f"来源: {factor.source_kind} · "
            f"交付: {_short_id(factor.delivery_id, length=16)}"
        )

    st.markdown("### 只读运行顺序")
    st.info("同步行情 → FacDigger 推理 → 导入 FactorBatch → 启动 TradingNode")
    st.caption("本页面只说明依赖顺序; 日常调度与操作入口不在当前阶段实现。")


def main() -> None:
    """按页面延迟读取本地数据并渲染五个只读页面。"""
    st.set_page_config(
        page_title="HeyBoss · Trading Assistant",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    st.title("HeyBoss")
    st.caption("Trading Assistant · PAPER 只读运营台 · 不包含审批、下单或撤单入口")
    st.sidebar.markdown("### HeyBoss")
    st.sidebar.caption("PAPER 环境 · 只读")
    page = st.sidebar.radio("导航", PAGES)

    database_url = os.environ.get("LIVE_DATABASE_URL", "sqlite:///./data/live.db")
    catalog_path = os.environ.get("CATALOG_PATH", "./catalog/eodhd")
    report_root = os.environ.get("REPORT_ROOT", "./reports/backtests")
    data_quality_root = os.environ.get("DATA_QUALITY_REPORT_ROOT", "./reports/data-quality")
    account = os.environ.get("TWS_ACCOUNT", "").strip()
    if not account:
        st.warning("未配置 TWS_ACCOUNT; 账户及该作用域的审计数据暂不显示。")
    account_id = f"IB-{account}"
    scope = f"paper:{account}"
    stale_seconds = int(os.environ.get("PORTFOLIO_SNAPSHOT_STALE_SECONDS", "90"))
    if page in ("总览", "Paper 交易", "策略与信号"):
        audit = _cached_audit(database_url, account_id, scope)
    if page in ("总览", "策略与信号"):
        factor = _cached_factor(catalog_path)
    if page == "总览":
        _render_overview(audit, factor, stale_seconds=stale_seconds)
    elif page == "Paper 交易":
        _render_paper_trading(audit, stale_seconds=stale_seconds)
    elif page == "策略与信号":
        signal_frame = _cached_signals(tuple(audit.signals), catalog_path, "1-DAY-LAST-EXTERNAL")
        _render_strategy_and_signals(audit, signal_frame, factor)
    elif page == "回测":
        _render_backtests(_cached_reports(report_root))
    else:
        _render_data_and_system(
            _cached_catalog_overview(catalog_path),
            _cached_factor(catalog_path),
            _cached_data_quality(data_quality_root),
        )


if __name__ == "__main__":
    main()
