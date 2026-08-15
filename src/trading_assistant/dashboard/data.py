"""Dashboard 的只读数据装配与事后收益计算。"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
from nautilus_trader.model.data import BarType, CustomData
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.factor import FactorScoreData
from trading_assistant.storage.repository import (
    PortfolioSnapshot,
    SignalReview,
    SignalWorkflow,
    TradingRepository,
)

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
WORKFLOW_COLUMNS = (
    "event_id",
    "strategy_name",
    "rebalance_key",
    "signal_timestamp_utc",
    "expires_at_utc",
    "status",
    "target_count",
    "planned_order_count",
    "risk_summary",
    "reason",
)
FILL_COLUMNS = (
    "timestamp_utc",
    "event_id",
    "strategy_name",
    "instrument_id",
    "client_order_id",
    "trade_id",
    "direction",
    "quantity",
    "price",
    "commission",
)
ACCOUNT_COLUMNS = (
    "timestamp_utc",
    "account_id",
    "currency",
    "net_liquidation",
    "free_cash",
    "locked_cash",
)
FACTOR_COLUMNS = (
    "rank",
    "canonical_id",
    "security_id",
    "score",
    "eligible",
)
CATALOG_COLUMNS = (
    "bar_type",
    "instrument_id",
    "source",
    "bar_count",
    "first_timestamp_utc",
    "last_timestamp_utc",
)
QUALITY_ISSUE_COLUMNS = (
    "timestamp_utc",
    "severity",
    "code",
    "instrument_id",
    "message",
)
QUALITY_CODE_COUNT_COLUMNS = ("severity", "code", "count")
QUALITY_INSTRUMENT_COUNT_COLUMNS = ("severity", "instrument_id", "count")
TIMELINE_COLUMNS = (
    "timestamp_utc",
    "event_id",
    "event_type",
    "status",
    "instrument_id",
    "direction",
    "quantity",
    "price",
    "detail",
    "client_order_id",
)


@dataclass(frozen=True)
class AuditViews:
    """Dashboard 一次刷新读取到的审计视图。"""

    portfolio: PortfolioSnapshot | None
    signals: tuple[SignalReview, ...]
    workflows: tuple[SignalWorkflow, ...]
    decisions: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    account_history: pd.DataFrame


@dataclass(frozen=True)
class BacktestReportView:
    """一个可浏览的回测报告目录。"""

    run_id: str
    summary: dict[str, Any]
    returns: pd.DataFrame
    fills: pd.DataFrame
    orders: pd.DataFrame
    positions: pd.DataFrame
    account: pd.DataFrame
    load_error: str | None = None


@dataclass(frozen=True)
class FactorSnapshotView:
    """最新完整 FactorScoreData 横截面的只读视图。"""

    asof_date: str
    available_at_utc: datetime
    batch_id: str
    batch_size: int
    delivery_id: str
    model_release_id: str
    source_kind: str
    scores: pd.DataFrame


@dataclass(frozen=True)
class DataQualityReportView:
    """最新一次历史数据任务的质量摘要与问题明细。"""

    report_name: str
    generated_at_utc: datetime | None
    mode: str | None
    bars_fetched: int | None
    bars_written: int | None
    corporate_actions_written: int | None
    report_entry_count: int
    error_count: int
    warning_count: int
    issues: pd.DataFrame
    issue_counts: pd.DataFrame
    instrument_issue_counts: pd.DataFrame
    load_error: str | None = None


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
        workflows=(),
        decisions=pd.DataFrame(columns=DECISION_COLUMNS),
        orders=pd.DataFrame(columns=ORDER_COLUMNS),
        fills=pd.DataFrame(columns=FILL_COLUMNS),
        account_history=pd.DataFrame(columns=ACCOUNT_COLUMNS),
    )
    if not _database_available(database_url):
        return empty
    repository = TradingRepository(database_url, read_only=True)
    try:
        portfolio = repository.latest_portfolio_snapshot(account_id=account_id)
        signals = repository.list_signal_reviews(scope=scope)
        workflows = repository.list_signal_workflows(scope=scope)
        decisions = pd.DataFrame.from_records(
            [audit.__dict__ for audit in repository.list_decision_audits(scope=scope)],
            columns=DECISION_COLUMNS,
        )
        orders = pd.DataFrame.from_records(
            [audit.__dict__ for audit in repository.list_order_audits(scope=scope)],
            columns=ORDER_COLUMNS,
        )
        fills = pd.DataFrame.from_records(
            [audit.__dict__ for audit in repository.list_fill_audits(scope=scope)],
            columns=FILL_COLUMNS,
        )
        account_history = pd.DataFrame.from_records(
            [audit.__dict__ for audit in repository.list_account_snapshots(account_id=account_id)],
            columns=ACCOUNT_COLUMNS,
        )
    except SQLAlchemyError:
        return empty
    finally:
        repository.close()
    return AuditViews(
        portfolio=portfolio,
        signals=signals,
        workflows=workflows,
        decisions=decisions,
        orders=orders,
        fills=fills,
        account_history=account_history,
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
    catalog = CatalogRepository(catalog_path) if catalog_path.exists() else None
    close_cache: dict[str, pd.Series[float]] = {}
    records: list[dict[str, object]] = []
    for review in reviews:
        closes = close_cache.get(review.instrument_id)
        if closes is None and review.instrument_id != "CASH.USD":
            bars = (
                catalog.read_bars(BarType.from_str(f"{review.instrument_id}-{bar_type_suffix}"))
                if catalog is not None
                else []
            )
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


def workflow_summary_frame(workflows: tuple[SignalWorkflow, ...]) -> pd.DataFrame:
    """把工作流转换为适合列表展示的稳定字段。"""
    records = [
        {
            "event_id": workflow.event_id,
            "strategy_name": workflow.strategy_name,
            "rebalance_key": workflow.rebalance_key,
            "signal_timestamp_utc": workflow.signal_timestamp_utc,
            "expires_at_utc": workflow.expires_at_utc,
            "status": workflow.status,
            "target_count": len(workflow.target_weights),
            "planned_order_count": len(workflow.planned_orders),
            "risk_summary": workflow.risk_summary,
            "reason": workflow.reason,
        }
        for workflow in workflows
    ]
    return pd.DataFrame.from_records(records, columns=WORKFLOW_COLUMNS)


def _event_records(frame: pd.DataFrame, event_id: str) -> list[dict[str, Any]]:
    if "event_id" not in frame:
        return []
    return cast(
        list[dict[str, Any]],
        frame[frame["event_id"] == event_id].to_dict(orient="records"),
    )


def workflow_timeline(audit: AuditViews, *, event_id: str) -> pd.DataFrame:
    """关联一个信号事件的生成、审批、订单与成交审计记录。"""
    records: list[dict[str, object]] = []
    workflow = next((value for value in audit.workflows if value.event_id == event_id), None)
    if workflow is not None:
        records.append(
            {
                "timestamp_utc": workflow.signal_timestamp_utc,
                "event_id": workflow.event_id,
                "event_type": "信号",
                "status": "GENERATED",
                "instrument_id": "",
                "direction": "",
                "quantity": None,
                "price": None,
                "detail": workflow.reason,
                "client_order_id": "",
            }
        )

    records.extend(
        {
            "timestamp_utc": decision["timestamp_utc"],
            "event_id": event_id,
            "event_type": "审批/风控",
            "status": decision["decision"],
            "instrument_id": "",
            "direction": "",
            "quantity": None,
            "price": None,
            "detail": decision["reason"],
            "client_order_id": "",
        }
        for decision in _event_records(audit.decisions, event_id)
    )

    records.extend(
        {
            "timestamp_utc": order["timestamp_utc"],
            "event_id": event_id,
            "event_type": "订单",
            "status": order["status"],
            "instrument_id": order["instrument_id"],
            "direction": order["direction"],
            "quantity": order["quantity"],
            "price": None,
            "detail": order["reason"],
            "client_order_id": order["client_order_id"],
        }
        for order in _event_records(audit.orders, event_id)
    )

    records.extend(
        {
            "timestamp_utc": fill["timestamp_utc"],
            "event_id": event_id,
            "event_type": "成交",
            "status": "FILLED",
            "instrument_id": fill["instrument_id"],
            "direction": fill["direction"],
            "quantity": fill["quantity"],
            "price": fill["price"],
            "detail": f"commission={float(fill['commission']):.4f}",
            "client_order_id": fill["client_order_id"],
        }
        for fill in _event_records(audit.fills, event_id)
    )

    frame = pd.DataFrame.from_records(records, columns=TIMELINE_COLUMNS)
    if not frame.empty:
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
        frame = frame.sort_values("timestamp_utc", kind="stable").reset_index(drop=True)
    return frame


def load_factor_snapshot(catalog_path: Path) -> FactorSnapshotView | None:
    """从 NT Catalog 读取时间最新且完整的因子横截面。"""
    if not catalog_path.exists():
        return None
    catalog = CatalogRepository(catalog_path)
    raw_values = catalog.catalog.query(FactorScoreData)
    batches: dict[str, list[FactorScoreData]] = defaultdict(list)
    for raw_value in raw_values:
        value = raw_value.data if isinstance(raw_value, CustomData) else raw_value
        if not isinstance(value, FactorScoreData):
            raise TypeError("NT Catalog returned an unexpected factor data type")
        batches[value.batch_id].append(value)

    complete: list[tuple[FactorScoreData, ...]] = []
    for values in batches.values():
        representative = values[0]
        if len(values) != representative.batch_size:
            continue
        if len({value.canonical_id for value in values}) != len(values):
            continue
        if any(
            value.batch_size != representative.batch_size
            or value.asof_date != representative.asof_date
            or value.delivery_id != representative.delivery_id
            or value.model_release_id != representative.model_release_id
            or value.source_kind != representative.source_kind
            or value.ts_event != representative.ts_event
            for value in values
        ):
            continue
        complete.append(tuple(values))
    if not complete:
        return None

    latest = max(
        complete,
        key=lambda values: (max(value.ts_event for value in values), values[0].batch_id),
    )
    representative = latest[0]
    ranked = sorted(
        latest,
        key=lambda value: (
            not value.eligible,
            -value.score if value.eligible else 0.0,
            value.canonical_id,
        ),
    )
    rank_by_id = {
        value.canonical_id: rank
        for rank, value in enumerate((item for item in ranked if item.eligible), start=1)
    }
    records = [
        {
            "rank": rank_by_id.get(value.canonical_id),
            "canonical_id": value.canonical_id,
            "security_id": value.security_id,
            "score": value.score if value.eligible else None,
            "eligible": value.eligible,
        }
        for value in ranked
    ]
    scores = pd.DataFrame.from_records(records, columns=FACTOR_COLUMNS)
    scores["rank"] = scores["rank"].astype("Int64")
    available_at_ns = max(value.ts_event for value in latest)
    return FactorSnapshotView(
        asof_date=representative.asof_date,
        available_at_utc=datetime.fromtimestamp(available_at_ns / 1_000_000_000, tz=UTC),
        batch_id=representative.batch_id,
        batch_size=representative.batch_size,
        delivery_id=representative.delivery_id,
        model_release_id=representative.model_release_id,
        source_kind=representative.source_kind,
        scores=scores,
    )


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


def load_catalog_overview(catalog_path: Path) -> pd.DataFrame:
    """按 NT BarType 汇总 Catalog 已实际存储的日线覆盖。"""
    if not catalog_path.exists():
        return pd.DataFrame(columns=CATALOG_COLUMNS)
    catalog = CatalogRepository(catalog_path)
    grouped: dict[str, dict[str, object]] = {}
    for bar in catalog.catalog.bars():
        identifier = str(bar.bar_type)
        current = grouped.get(identifier)
        if current is None:
            grouped[identifier] = {
                "bar_type": identifier,
                "instrument_id": str(bar.bar_type.instrument_id),
                "source": ("INTERNAL" if bar.bar_type.is_internally_aggregated() else "EXTERNAL"),
                "bar_count": 1,
                "first_timestamp_utc": pd.Timestamp(bar.ts_init, unit="ns", tz="UTC"),
                "last_timestamp_utc": pd.Timestamp(bar.ts_init, unit="ns", tz="UTC"),
            }
            continue
        current["bar_count"] = cast(int, current["bar_count"]) + 1
        timestamp = pd.Timestamp(bar.ts_init, unit="ns", tz="UTC")
        current["first_timestamp_utc"] = min(
            cast(pd.Timestamp, current["first_timestamp_utc"]), timestamp
        )
        current["last_timestamp_utc"] = max(
            cast(pd.Timestamp, current["last_timestamp_utc"]), timestamp
        )
    return (
        pd.DataFrame.from_records(list(grouped.values()), columns=CATALOG_COLUMNS)
        .sort_values(["instrument_id", "source"], kind="stable")
        .reset_index(drop=True)
    )


def _empty_quality_report(report_name: str, error: str) -> DataQualityReportView:
    return DataQualityReportView(
        report_name=report_name,
        generated_at_utc=None,
        mode=None,
        bars_fetched=None,
        bars_written=None,
        corporate_actions_written=None,
        report_entry_count=0,
        error_count=0,
        warning_count=0,
        issues=pd.DataFrame(columns=QUALITY_ISSUE_COLUMNS),
        issue_counts=pd.DataFrame(columns=QUALITY_CODE_COUNT_COLUMNS),
        instrument_issue_counts=pd.DataFrame(columns=QUALITY_INSTRUMENT_COUNT_COLUMNS),
        load_error=error,
    )


def _quality_issue_record(value: object, *, fallback_instrument: str = "") -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("质量问题不是对象")
    code = value.get("code")
    severity = value.get("severity")
    message = value.get("message")
    if not isinstance(code, str) or not isinstance(severity, str) or not isinstance(message, str):
        raise ValueError("质量问题缺少 code、severity 或 message")
    instrument = value.get("instrument_id", fallback_instrument)
    if not isinstance(instrument, str):
        raise ValueError("质量问题的 instrument_id 无效")
    timestamp_ns = value.get("timestamp_ns")
    if timestamp_ns is not None and not isinstance(timestamp_ns, int):
        raise ValueError("质量问题的 timestamp_ns 无效")
    return {
        "timestamp_utc": (
            pd.Timestamp(timestamp_ns, unit="ns", tz="UTC") if timestamp_ns is not None else None
        ),
        "severity": severity,
        "code": code,
        "instrument_id": instrument or fallback_instrument,
        "message": message,
    }


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        raise ValueError(f"{key} 不是整数")
    return value


def load_latest_data_quality(report_root: Path) -> DataQualityReportView | None:
    """读取最新质量报告, 并聚合顶层及逐序列问题。"""
    if not report_root.exists():
        return None
    paths = sorted(report_root.glob("data-quality-*.json"), reverse=True)
    if not paths:
        return None
    path = paths[0]
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("报告根节点不是对象")
        payload = cast(dict[str, Any], loaded)
        generated_raw = payload.get("generated_at_utc")
        generated = (
            datetime.fromisoformat(generated_raw.replace("Z", "+00:00"))
            if isinstance(generated_raw, str)
            else None
        )
        mode_raw = payload.get("mode")
        mode = mode_raw if isinstance(mode_raw, str) else None
        top_issues = payload.get("issues", [])
        instruments = payload.get("instruments", [])
        if not isinstance(top_issues, list) or not isinstance(instruments, list):
            raise ValueError("issues 或 instruments 不是列表")
        records = [_quality_issue_record(issue) for issue in top_issues]
        for instrument in instruments:
            if not isinstance(instrument, dict):
                raise ValueError("instruments 包含非对象记录")
            instrument_id = instrument.get("instrument_id", "")
            nested_issues = instrument.get("issues", [])
            if not isinstance(instrument_id, str) or not isinstance(nested_issues, list):
                raise ValueError("标的质量记录结构无效")
            records.extend(
                _quality_issue_record(issue, fallback_instrument=instrument_id)
                for issue in nested_issues
            )
        issues = pd.DataFrame.from_records(records, columns=QUALITY_ISSUE_COLUMNS)
        issue_counts = (
            issues.groupby(["severity", "code"], dropna=False)
            .size()
            .reset_index(name="count")
            .sort_values(["severity", "count", "code"], ascending=[True, False, True])
            .reset_index(drop=True)
            if not issues.empty
            else pd.DataFrame(columns=QUALITY_CODE_COUNT_COLUMNS)
        )
        instrument_counts = (
            issues.groupby(["severity", "instrument_id"], dropna=False)
            .size()
            .reset_index(name="count")
            .sort_values(["severity", "count", "instrument_id"], ascending=[True, False, True])
            .reset_index(drop=True)
            if not issues.empty
            else pd.DataFrame(columns=QUALITY_INSTRUMENT_COUNT_COLUMNS)
        )
        return DataQualityReportView(
            report_name=path.name,
            generated_at_utc=generated,
            mode=mode,
            bars_fetched=_optional_int(payload, "bars_fetched"),
            bars_written=_optional_int(payload, "bars_written"),
            corporate_actions_written=_optional_int(payload, "corporate_actions_written"),
            report_entry_count=len(instruments),
            error_count=int((issues["severity"] == "error").sum()),
            warning_count=int((issues["severity"] == "warning").sum()),
            issues=issues,
            issue_counts=issue_counts,
            instrument_issue_counts=instrument_counts,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _empty_quality_report(path.name, f"无法读取最新数据质量报告: {exc}")


def _read_optional_csv(path: Path) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def load_backtest_reports(report_root: Path) -> tuple[BacktestReportView, ...]:
    """只读加载回测生成的稳定 JSON/CSV 报告。"""
    if not report_root.exists():
        return ()
    reports: list[BacktestReportView] = []
    for directory in sorted(report_root.iterdir(), reverse=True):
        summary_path = directory / "summary.json"
        if not directory.is_dir() or not summary_path.is_file():
            continue
        try:
            raw_summary: object = json.loads(summary_path.read_text(encoding="utf-8"))
            if not isinstance(raw_summary, dict):
                raise ValueError("summary.json 根节点不是对象")
            reports.append(
                BacktestReportView(
                    run_id=directory.name,
                    summary={str(key): value for key, value in raw_summary.items()},
                    returns=_read_optional_csv(directory / "returns.csv"),
                    fills=_read_optional_csv(directory / "fills.csv"),
                    orders=_read_optional_csv(directory / "orders.csv"),
                    positions=_read_optional_csv(directory / "positions.csv"),
                    account=_read_optional_csv(directory / "account.csv"),
                )
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            pd.errors.ParserError,
            ValueError,
        ) as exc:
            reports.append(
                BacktestReportView(
                    run_id=directory.name,
                    summary={},
                    returns=pd.DataFrame(),
                    fills=pd.DataFrame(),
                    orders=pd.DataFrame(),
                    positions=pd.DataFrame(),
                    account=pd.DataFrame(),
                    load_error=f"报告文件损坏或结构无效: {exc}",
                )
            )
    return tuple(reports)
