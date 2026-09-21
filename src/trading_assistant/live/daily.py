"""固定生产因子的每日接纳进程; 只同步数据, 不连接券商或计算交易。"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from trading_assistant.data.config import load_data_config, load_instruments
from trading_assistant.data.factor import (
    ValidatedFactorBundle,
    expected_factor_date,
    factor_execution_window,
    import_factor_bundle,
    resolve_factor_rows,
    validate_factor_bundle,
)
from trading_assistant.data.pipeline import PipelineSummary
from trading_assistant.data.service import sync_historical_specs
from trading_assistant.live.config import load_live_settings
from trading_assistant.storage.repository import TradingRepository
from trading_assistant.strategies.config import PatchTSTFactorSettings, load_active_strategy

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperInputResult:
    """本轮交接结果; 成功接纳依据仍只保存在 factor_imports。"""

    asof_date: date
    action: Literal["waiting", "accepted", "already_accepted", "cutoff", "blocked"]
    reason: str
    delivery_id: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _database_url(project_root: Path, environ: Mapping[str, str]) -> str:
    return environ.get(
        "LIVE_DATABASE_URL", f"sqlite:///{project_root / 'runtime' / 'data' / 'live.db'}"
    )


@contextmanager
def paper_input_lock(database_url: str) -> Iterator[None]:
    """整个服务生命周期独占一个输入消费者, 进程退出由操作系统释放。"""
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        raise ValueError("Paper input requires a local SQLite database")
    directory = Path(database_url.removeprefix("sqlite:///")).expanduser().resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".paper-input.lock").open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another paper input consumer is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _find_bundle(
    root: Path, asof_date: date, release_id: str
) -> tuple[Path, ValidatedFactorBundle] | None:
    """只消费已完成目录; 预筛选仅避免校验无关历史, 完整契约仍由导入器检查。"""
    if not root.is_dir():
        raise ValueError("FACTOR_BATCH_PATH is not an available directory")
    candidates: list[tuple[Path, ValidatedFactorBundle]] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir() or re.fullmatch(r"[0-9a-f]{64}", path.name) is None:
            continue
        if not (path / "manifest.json").is_file() or not (path / "factors.parquet").is_file():
            continue
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("Finalized FactorBatch manifest must be an object")
        model = manifest.get("model")
        times = manifest.get("time")
        if not isinstance(model, dict) or not isinstance(times, dict):
            raise ValueError("Finalized FactorBatch is missing model or time metadata")
        if model.get("release_id") != release_id or times.get("maximum_asof_date") != str(
            asof_date
        ):
            continue
        bundle = validate_factor_bundle(path)
        if (
            bundle.metadata.source_kind != "signal_inference"
            or bundle.metadata.minimum_asof_date != asof_date
        ):
            raise ValueError("Paper input requires one complete signal_inference date")
        candidates.append((path, bundle))
    if len(candidates) > 1:
        raise ValueError("Conflicting finalized deliveries exist for the expected factor date")
    return candidates[0] if candidates else None


def _check_quality(summary: PipelineSummary, unscorable: set[str]) -> None:
    """只有明确缺分标的允许缺价; 认证、格式与价格质量错误不能被豁免。"""
    issues = [*summary.issues]
    for report in summary.instrument_reports:
        issues.extend(report.issues)
    for issue in issues:
        if issue.severity != "error":
            continue
        if issue.instrument_id in unscorable and issue.code in {
            "start_coverage_missing",
            "no_data",
            "stale_data",
        }:
            continue
        raise ValueError(f"Price preparation failed: {issue.instrument_id}: {issue.code}")


def _run_paper_input_tick(
    *, project_root: Path, environ: Mapping[str, str], now: datetime
) -> PaperInputResult:
    asof_date = expected_factor_date(now)
    delivery_id: str | None = None
    try:
        strategy = load_active_strategy(project_root / "config" / "strategies.yaml")
        if not isinstance(strategy.settings, PatchTSTFactorSettings):
            raise ValueError("Paper input requires the configured factor strategy")
        release_id = strategy.settings.model_release_id
        if release_id is None or strategy.settings.allow_evaluation_predictions:
            raise ValueError("Paper input requires a fixed production model release")
        source = environ.get("FACTOR_BATCH_PATH", "").strip()
        if not source:
            raise ValueError("FACTOR_BATCH_PATH is required")
        selected = _find_bundle(Path(source).expanduser().resolve(), asof_date, release_id)
        if selected is None:
            cutoff, _ = factor_execution_window(asof_date, strategy.settings.signal_expiry_hours)
            if now >= cutoff:
                return PaperInputResult(
                    asof_date, "cutoff", "Expected production batch missed acceptance cutoff"
                )
            return PaperInputResult(asof_date, "waiting", "Expected production batch is not ready")
        path, bundle = selected
        delivery_id = bundle.metadata.delivery_id
        instruments = load_instruments(project_root / "config" / "instruments.yaml")
        daily_rows = resolve_factor_rows(bundle, instruments)[asof_date]
        catalog_path = (
            Path(environ.get("CATALOG_PATH", str(project_root / "runtime" / "catalog" / "eodhd")))
            .expanduser()
            .resolve()
        )
        repository = TradingRepository(_database_url(project_root, environ))
        try:
            repository.create_schema()
            accepted = repository.get_factor_import(
                catalog_path=str(catalog_path), delivery_id=delivery_id, mode="paper"
            )
            if accepted is not None:
                return PaperInputResult(
                    asof_date, "already_accepted", "Already accepted", delivery_id
                )
            cutoff, _ = factor_execution_window(asof_date, strategy.settings.signal_expiry_hours)
            if now >= cutoff:
                return PaperInputResult(
                    asof_date, "cutoff", "Paper acceptance cutoff reached", delivery_id
                )
            data_config_path = project_root / "config" / "data.yaml"
            data = load_data_config(data_config_path)
            if data.historical_data.provider != "eodhd":
                raise ValueError("Paper input only supports EODHD; broker data access is forbidden")
            summary = asyncio.run(
                sync_historical_specs(
                    catalog_path=catalog_path,
                    report_directory=Path(
                        environ.get(
                            "DATA_QUALITY_REPORT_ROOT",
                            str(project_root / "runtime" / "reports" / "data-quality"),
                        )
                    ),
                    data_config_path=data_config_path,
                    instruments=tuple(spec for spec, _ in daily_rows),
                    start_date=None,
                    end_date=asof_date,
                    eodhd_api_token=environ.get("EODHD_API_TOKEN"),
                    write_mode="replace_full",
                )
            )
            _check_quality(
                summary, {spec.canonical_id for spec, row in daily_rows if not row.eligible}
            )
            import_factor_bundle(
                bundle_dir=path,
                catalog_path=catalog_path,
                instruments=instruments,
                signal_bar_type_suffix=data.historical_data.signal_bar_type_suffix,
                execution_bar_type_suffix=data.historical_data.execution_bar_type_suffix,
                mode="paper",
                repository=repository,
                expected_release_id=release_id,
                clock=_utc_now,
            )
            return PaperInputResult(asof_date, "accepted", "Production batch accepted", delivery_id)
        finally:
            repository.close()
    except (ValueError, OSError, RuntimeError, SQLAlchemyError) as exc:
        return PaperInputResult(asof_date, "blocked", str(exc), delivery_id)


def run_paper_input_tick(
    *, project_root: Path, environ: Mapping[str, str], now: datetime
) -> PaperInputResult:
    """执行一次唯一接纳流程; now 不参与最终接纳时刻的写入。"""
    with paper_input_lock(_database_url(project_root, environ)):
        return _run_paper_input_tick(project_root=project_root, environ=environ, now=now)


def serve_paper_inputs(*, project_root: Path, environ: Mapping[str, str]) -> None:
    """常驻消费; 瞬时失败有界重试, 不重放订单也不修改已接纳交付。"""
    settings = load_live_settings(project_root / "config" / "live.yaml")
    previous: PaperInputResult | None = None
    with paper_input_lock(_database_url(project_root, environ)):
        while True:
            result = _run_paper_input_tick(
                project_root=project_root, environ=environ, now=_utc_now()
            )
            if result != previous:
                LOGGER.info(
                    "Paper input: date=%s action=%s reason=%s",
                    result.asof_date,
                    result.action,
                    result.reason,
                )
                previous = result
            sleep(
                settings.paper_input_retry_interval_seconds
                if result.action == "blocked"
                else settings.paper_input_poll_interval_seconds
            )
