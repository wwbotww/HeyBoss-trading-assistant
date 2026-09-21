"""策略、因子、回测与历史数据的只读查询服务。"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from nautilus_trader.model.data import BarType, CustomData
from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.application.models import (
    BacktestDetailView,
    BacktestRunView,
    CatalogCoverageView,
    CatalogView,
    DataQualityView,
    FactorScoreView,
    FactorSnapshotView,
    Page,
    QualityInstrumentView,
    QualityIssueView,
    QuerySourceError,
    ReportTableView,
    ResourceNotFoundError,
    Scalar,
    SourceState,
    StrategyView,
)
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import DataPipelineConfig, load_data_config, load_instruments
from trading_assistant.data.factor import FactorScoreData
from trading_assistant.risk.config import load_risk_limits
from trading_assistant.signals.factor import calculate_factor_weights
from trading_assistant.storage.repository import BacktestRunAudit, TradingRepository
from trading_assistant.strategies.config import (
    PatchTSTFactorSettings,
    load_active_strategy,
)

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REPORT_TABLES = {
    "equity": "returns.csv",
    "orders": "orders.csv",
    "fills": "fills.csv",
    "positions": "positions.csv",
    "account": "account.csv",
}
_BACKTEST_SCAN_LIMIT = 5_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _datetime_from_ns(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000_000, tz=UTC)


def _safe_scalars(values: dict[str, Any]) -> dict[str, Scalar]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


class ResearchQueryService:
    """组合白名单配置、NT Catalog、回测库与报告目录。"""

    def __init__(
        self,
        *,
        backtest_repository: TradingRepository | None,
        project_root: Path,
        catalog_path: Path,
        report_root: Path,
        quality_report_root: Path,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._backtest_repository = backtest_repository
        self._project_root = project_root
        self._catalog_path = catalog_path
        self._report_root = report_root
        self._quality_report_root = quality_report_root
        self._clock = clock

    def active_strategy(self) -> StrategyView:
        """读取已校验的活动策略和统一风控阈值。"""
        now = self._clock().astimezone(UTC)
        strategy_path = self._project_root / "config" / "strategies.yaml"
        risk_path = self._project_root / "config" / "risk.yaml"
        if not strategy_path.is_file() or not risk_path.is_file():
            return StrategyView(
                source_state="missing",
                observed_at_utc=now,
                name=None,
                approval_mode=None,
                signal_expiry_hours=None,
                parameters={},
                risk_limits={},
            )
        try:
            strategy = load_active_strategy(strategy_path)
            risk = load_risk_limits(risk_path)
        except (OSError, ValueError):
            return StrategyView(
                source_state="invalid",
                observed_at_utc=now,
                name=None,
                approval_mode=None,
                signal_expiry_hours=None,
                parameters={},
                risk_limits={},
            )
        settings = asdict(strategy.settings)
        approval_mode = cast(str, settings.pop("approval_mode"))
        signal_expiry_hours = cast(int, settings.pop("signal_expiry_hours"))
        return StrategyView(
            source_state="available",
            observed_at_utc=now,
            name=strategy.name,
            approval_mode=approval_mode,
            signal_expiry_hours=signal_expiry_hours,
            parameters=_safe_scalars(settings),
            risk_limits=_safe_scalars(asdict(risk)),
        )

    def latest_factor(self) -> FactorSnapshotView:
        """返回 Catalog 中最近一个原子完整的横截面批次。"""
        now = self._clock().astimezone(UTC)
        if not self._catalog_path.is_dir():
            return self._empty_factor("missing", now)
        try:
            values = CatalogRepository(self._catalog_path).catalog.query(FactorScoreData)
            scores = tuple(self._factor_payload(value) for value in values)
        except Exception:  # NT Catalog 的底层异常类型并不稳定。
            return self._empty_factor("invalid", now)
        if not scores:
            return self._empty_factor("empty", now)
        batches: dict[str, list[FactorScoreData]] = defaultdict(list)
        for score in scores:
            batches[score.batch_id].append(score)
        complete = [rows for rows in batches.values() if self._complete_batch(rows)]
        if not complete:
            return self._empty_factor("invalid", now)
        latest = max(
            complete,
            key=lambda rows: (rows[0].ts_event, rows[0].asof_date, rows[0].batch_id),
        )
        latest.sort(key=lambda item: item.canonical_id)

        strategy_path = self._project_root / "config" / "strategies.yaml"
        try:
            configured = load_active_strategy(strategy_path)
            settings = configured.settings
            if isinstance(settings, PatchTSTFactorSettings):
                risk = load_risk_limits(self._project_root / "config" / "risk.yaml")
                decision = calculate_factor_weights(
                    {item.canonical_id: item.score if item.eligible else None for item in latest},
                    top_n=settings.top_n,
                    target_gross_exposure=settings.target_gross_exposure,
                    max_unscorable_fraction=risk.max_factor_unscorable_fraction,
                )
                weights = dict(decision.target_weights)
            else:
                weights = {}
        except (OSError, ValueError):
            weights = {}
        ranked_ids = sorted(
            (item.canonical_id for item in latest if item.eligible),
            key=lambda canonical_id: (
                -next(item.score for item in latest if item.canonical_id == canonical_id),
                canonical_id,
            ),
        )
        ranks = {canonical_id: index + 1 for index, canonical_id in enumerate(ranked_ids)}
        try:
            specs = load_instruments(self._project_root / "config" / "instruments.yaml")
        except (OSError, ValueError):
            specs = ()
        symbols = {spec.canonical_id: spec.symbol for spec in specs}
        rows = tuple(
            FactorScoreView(
                canonical_id=item.canonical_id,
                symbol=symbols.get(item.canonical_id, item.canonical_id.split(".", 1)[0]),
                security_id=item.security_id,
                score=item.score if item.eligible else None,
                eligible=item.eligible,
                rank=ranks.get(item.canonical_id),
                selected=item.canonical_id in weights,
                target_weight=weights.get(item.canonical_id, 0.0),
            )
            for item in sorted(
                latest,
                key=lambda value: (
                    ranks.get(value.canonical_id, len(latest) + 1),
                    value.canonical_id,
                ),
            )
        )
        first = latest[0]
        return FactorSnapshotView(
            source_state="available",
            observed_at_utc=now,
            asof_date=first.asof_date,
            available_at_utc=_datetime_from_ns(first.ts_event),
            batch_id=first.batch_id,
            delivery_id=first.delivery_id,
            model_release_id=first.model_release_id,
            source_kind=first.source_kind,
            expected_rows=first.batch_size,
            scores=rows,
        )

    def list_backtests(self, *, offset: int, limit: int) -> Page[BacktestRunView]:
        """合并回测审计状态和报告目录。"""
        audits = self._backtest_audits()
        audit_by_id = {item.run_id: item for item in audits}
        directory_ids = self._report_run_ids()
        run_ids = set(audit_by_id) | set(directory_ids)
        runs = [self._backtest_view(run_id, audit_by_id.get(run_id)) for run_id in run_ids]
        runs.sort(
            key=lambda item: (
                item.started_at_utc or datetime.min.replace(tzinfo=UTC),
                item.run_id,
            ),
            reverse=True,
        )
        window = runs[offset : offset + limit + 1]
        return Page(
            items=tuple(window[:limit]),
            offset=offset,
            limit=limit,
            has_more=len(window) > limit,
        )

    def backtest_detail(self, run_id: str) -> BacktestDetailView:
        """读取一个回测的安全摘要。"""
        self._validate_run_id(run_id)
        audit = self._backtest_run(run_id)
        directory = self._report_root / run_id
        if audit is None and not directory.is_dir():
            raise ResourceNotFoundError("回测运行不存在")
        summary, _ = self._report_summary(run_id)
        available_tables = tuple(
            name for name, filename in _REPORT_TABLES.items() if (directory / filename).is_file()
        )
        return BacktestDetailView(
            run=self._backtest_view(run_id, audit),
            summary={} if summary is None else _safe_scalars(summary),
            available_tables=available_tables,
        )

    def backtest_table(
        self,
        run_id: str,
        table: str,
        *,
        offset: int,
        limit: int,
    ) -> ReportTableView:
        """分页读取固定白名单中的回测 CSV。"""
        self._validate_run_id(run_id)
        filename = _REPORT_TABLES.get(table)
        if filename is None:
            raise ResourceNotFoundError("回测表不存在")
        path = self._report_root / run_id / filename
        if not path.is_file():
            raise ResourceNotFoundError("回测表不存在")
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise ValueError("missing CSV header")
                columns = tuple(column if column else "index" for column in reader.fieldnames)
                selected: list[dict[str, Scalar]] = []
                for index, raw in enumerate(reader):
                    if index < offset:
                        continue
                    if len(selected) >= limit + 1:
                        break
                    selected.append(
                        {
                            (key if key else "index"): self._csv_scalar(value)
                            for key, value in raw.items()
                            if key is not None
                        }
                    )
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            raise QuerySourceError("backtest_report", "回测报告无法读取") from exc
        return ReportTableView(
            columns=columns,
            rows=tuple(selected[:limit]),
            offset=offset,
            limit=limit,
            has_more=len(selected) > limit,
        )

    def catalog_coverage(self) -> CatalogView:
        """读取配置股票池的 signal/execution 日线边界。"""
        now = self._clock().astimezone(UTC)
        try:
            instruments = load_instruments(self._project_root / "config" / "instruments.yaml")
            data_config = load_data_config(self._project_root / "config" / "data.yaml")
        except FileNotFoundError:
            return CatalogView("missing", now, "unknown", ())
        except (OSError, ValueError):
            return CatalogView("invalid", now, "unknown", ())
        if not self._catalog_path.is_dir():
            return CatalogView(
                source_state="missing",
                observed_at_utc=now,
                provider=data_config.historical_data.provider,
                coverage=tuple(
                    CatalogCoverageView(
                        instrument_id=spec.canonical_id,
                        symbol=spec.symbol,
                        price_kind=kind,
                        bar_type=f"{spec.canonical_id}-{suffix}",
                        state="missing",
                        first_at_utc=None,
                        last_at_utc=None,
                    )
                    for spec in instruments
                    for kind, suffix in self._bar_suffixes(data_config)
                ),
            )
        try:
            catalog = CatalogRepository(self._catalog_path)
            coverage = tuple(
                self._coverage_row(catalog, spec.canonical_id, spec.symbol, kind, suffix)
                for spec in instruments
                for kind, suffix in self._bar_suffixes(data_config)
            )
        except Exception:  # NT Catalog 的底层异常类型并不稳定。
            return CatalogView("invalid", now, data_config.historical_data.provider, ())
        overall: SourceState = (
            "available" if any(row.state == "available" for row in coverage) else "empty"
        )
        return CatalogView(
            source_state=overall,
            observed_at_utc=now,
            provider=data_config.historical_data.provider,
            coverage=coverage,
        )

    def latest_data_quality(self) -> DataQualityView:
        """读取最新一份数据质量报告并严格限制输出字段。"""
        now = self._clock().astimezone(UTC)
        if not self._quality_report_root.is_dir():
            return self._empty_quality("missing", now)
        candidates = sorted(self._quality_report_root.glob("data-quality-*.json"))
        if not candidates:
            return self._empty_quality("empty", now)
        try:
            loaded = json.loads(candidates[-1].read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("report root must be an object")
            payload = cast(dict[str, Any], loaded)
            generated_at = self._parse_utc_datetime(payload["generated_at_utc"])
            issues = [self._quality_issue(item) for item in self._list(payload["issues"])]
            instruments: list[QualityInstrumentView] = []
            for raw in self._list(payload["instruments"]):
                item = self._mapping(raw)
                nested = [self._quality_issue(value) for value in self._list(item["issues"])]
                issues.extend(nested)
                instruments.append(
                    QualityInstrumentView(
                        instrument_id=self._text(item["instrument_id"]),
                        bar_count=self._integer(item["bar_count"]),
                        first_at_utc=_datetime_from_ns(
                            self._optional_integer(item["first_timestamp_ns"])
                        ),
                        last_at_utc=_datetime_from_ns(
                            self._optional_integer(item["last_timestamp_ns"])
                        ),
                        issue_count=len(nested),
                    )
                )
            error_count = sum(issue.severity == "error" for issue in issues)
            warning_count = sum(issue.severity == "warning" for issue in issues)
            return DataQualityView(
                source_state="available",
                observed_at_utc=now,
                generated_at_utc=generated_at,
                mode=self._text(payload["mode"]),
                bars_fetched=self._integer(payload["bars_fetched"]),
                bars_written=self._integer(payload["bars_written"]),
                corporate_actions_written=self._integer(payload["corporate_actions_written"]),
                error_count=error_count,
                warning_count=warning_count,
                issues=tuple(issues),
                instruments=tuple(instruments),
            )
        except (KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._empty_quality("invalid", now)

    def _backtest_audits(self) -> tuple[BacktestRunAudit, ...]:
        if self._backtest_repository is None:
            return ()
        try:
            return self._backtest_repository.list_backtest_runs(limit=_BACKTEST_SCAN_LIMIT)
        except SQLAlchemyError as exc:
            raise QuerySourceError("backtest_database", "回测审计库无法读取") from exc

    def _backtest_run(self, run_id: str) -> BacktestRunAudit | None:
        if self._backtest_repository is None:
            return None
        try:
            return self._backtest_repository.get_backtest_run(run_id)
        except SQLAlchemyError as exc:
            raise QuerySourceError("backtest_database", "回测审计库无法读取") from exc

    def _report_run_ids(self) -> tuple[str, ...]:
        if not self._report_root.is_dir():
            return ()
        return tuple(
            path.name
            for path in self._report_root.iterdir()
            if path.is_dir() and _RUN_ID_PATTERN.fullmatch(path.name) is not None
        )

    def _backtest_view(
        self,
        run_id: str,
        audit: BacktestRunAudit | None,
    ) -> BacktestRunView:
        summary, report_state = self._report_summary(run_id)
        values = summary or (audit.summary if audit is not None else None) or {}
        return BacktestRunView(
            run_id=run_id,
            started_at_utc=None if audit is None else audit.started_at,
            completed_at_utc=None if audit is None else audit.completed_at,
            status="REPORT_ONLY" if audit is None else audit.status,
            strategy_name=_optional_text(values.get("strategy")),
            evaluation_start=_optional_text(values.get("evaluation_start")),
            end=_optional_text(values.get("end")),
            final_equity_usd=_optional_float(values.get("final_equity_usd")),
            annualized_return=_optional_float(values.get("annualized_return")),
            max_drawdown=_optional_float(values.get("max_drawdown")),
            sharpe_ratio=_optional_float(values.get("sharpe_ratio")),
            report_state=report_state,
        )

    def _report_summary(
        self,
        run_id: str,
    ) -> tuple[dict[str, Any] | None, SourceState]:
        path = self._report_root / run_id / "summary.json"
        if not path.is_file():
            state: SourceState = "invalid" if path.parent.is_dir() else "missing"
            return None, state
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, "invalid"
        if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
            return None, "invalid"
        return cast(dict[str, Any], loaded), "available"

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if _RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise ResourceNotFoundError("回测运行不存在")

    @staticmethod
    def _factor_payload(value: object) -> FactorScoreData:
        payload = value.data if isinstance(value, CustomData) else value
        if not isinstance(payload, FactorScoreData):
            raise TypeError("unexpected factor payload")
        return payload

    @staticmethod
    def _complete_batch(rows: list[FactorScoreData]) -> bool:
        if not rows:
            return False
        first = rows[0]
        return (
            first.batch_size > 0
            and len(rows) == first.batch_size
            and len({row.canonical_id for row in rows}) == first.batch_size
            and all(
                row.batch_size == first.batch_size
                and row.asof_date == first.asof_date
                and row.delivery_id == first.delivery_id
                and row.model_release_id == first.model_release_id
                and row.source_kind == first.source_kind
                and row.ts_event == first.ts_event
                for row in rows
            )
        )

    @staticmethod
    def _empty_factor(state: SourceState, now: datetime) -> FactorSnapshotView:
        if state not in {"missing", "empty", "invalid"}:
            raise ValueError(f"Unexpected factor state: {state}")
        return FactorSnapshotView(
            source_state=state,
            observed_at_utc=now,
            asof_date=None,
            available_at_utc=None,
            batch_id=None,
            delivery_id=None,
            model_release_id=None,
            source_kind=None,
            expected_rows=0,
            scores=(),
        )

    @staticmethod
    def _bar_suffixes(
        data_config: DataPipelineConfig,
    ) -> tuple[tuple[Literal["signal", "execution"], str], ...]:
        historical = data_config.historical_data
        signal: tuple[Literal["signal", "execution"], str] = (
            "signal",
            historical.signal_bar_type_suffix,
        )
        execution: tuple[Literal["signal", "execution"], str] = (
            "execution",
            historical.execution_bar_type_suffix,
        )
        return signal, execution

    @staticmethod
    def _coverage_row(
        catalog: CatalogRepository,
        instrument_id: str,
        symbol: str,
        kind: Literal["signal", "execution"],
        suffix: str,
    ) -> CatalogCoverageView:
        bar_type = BarType.from_str(f"{instrument_id}-{suffix}")
        first = catalog.earliest_bar_timestamp(bar_type)
        last = catalog.latest_bar_timestamp(bar_type)
        state: SourceState = "available" if first is not None and last is not None else "empty"
        return CatalogCoverageView(
            instrument_id=instrument_id,
            symbol=symbol,
            price_kind=kind,
            bar_type=str(bar_type),
            state=state,
            first_at_utc=_datetime_from_ns(first),
            last_at_utc=_datetime_from_ns(last),
        )

    @staticmethod
    def _empty_quality(state: SourceState, now: datetime) -> DataQualityView:
        if state not in {"missing", "empty", "invalid"}:
            raise ValueError(f"Unexpected quality state: {state}")
        return DataQualityView(
            source_state=state,
            observed_at_utc=now,
            generated_at_utc=None,
            mode=None,
            bars_fetched=0,
            bars_written=0,
            corporate_actions_written=0,
            error_count=0,
            warning_count=0,
            issues=(),
            instruments=(),
        )

    @classmethod
    def _quality_issue(cls, raw: object) -> QualityIssueView:
        item = cls._mapping(raw)
        severity = cls._text(item["severity"])
        if severity not in {"warning", "error"}:
            raise ValueError("invalid issue severity")
        return QualityIssueView(
            code=cls._text(item["code"]),
            severity=severity,
            instrument_id=cls._text(item["instrument_id"]),
            timestamp_utc=_datetime_from_ns(cls._optional_integer(item["timestamp_ns"])),
            message=cls._text(item["message"]),
        )

    @staticmethod
    def _mapping(value: object) -> dict[str, Any]:
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise TypeError("expected object")
        return cast(dict[str, Any], value)

    @staticmethod
    def _list(value: object) -> list[object]:
        if not isinstance(value, list):
            raise TypeError("expected list")
        return cast(list[object], value)

    @staticmethod
    def _text(value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("expected string")
        return value

    @staticmethod
    def _integer(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("expected integer")
        return value

    @classmethod
    def _optional_integer(cls, value: object) -> int | None:
        return None if value is None else cls._integer(value)

    @staticmethod
    def _parse_utc_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise TypeError("expected datetime string")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone is required")
        return parsed.astimezone(UTC)

    @staticmethod
    def _csv_scalar(value: str | None) -> Scalar:
        if value is None or not value.strip():
            return None
        text = value.strip()
        if text.lower() in {"true", "false"}:
            return text.lower() == "true"
        try:
            integer = int(text)
        except ValueError:
            pass
        else:
            return integer
        try:
            number = float(text)
        except ValueError:
            return text
        return number if math.isfinite(number) else text
