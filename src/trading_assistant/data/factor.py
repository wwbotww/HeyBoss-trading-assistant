"""FacDigger FactorBatch 到 NT CustomData Catalog 的唯一适配边界。"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd
from nautilus_trader.core.data import Data
from nautilus_trader.core.nautilus_pyo3.model import register_custom_data_class
from nautilus_trader.model.custom import customdataclass_pyo3
from nautilus_trader.model.data import BarType, CustomData, DataType

from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec

FACTOR_CONTRACT = "facdigger.factor_batch"
FACTOR_COLUMNS = ("security_id", "symbol", "asof_date", "score", "eligible")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SOURCE_SEMANTICS = {
    "evaluation_predictions": "eligible_scored_cross_section",
    "signal_inference": "complete_candidate_cross_section",
}


@customdataclass_pyo3()  # type: ignore[no-untyped-call]
class FactorScoreData(Data):  # type: ignore[misc]
    """写入 NT Catalog 的逐证券因子分数。"""

    canonical_id: str
    security_id: str
    asof_date: str
    score: float
    eligible: bool
    batch_id: str
    batch_size: int
    delivery_id: str
    model_release_id: str
    source_kind: str


register_custom_data_class(FactorScoreData)
# NT 1.230 的 Catalog 历史请求不会把查询 metadata 带回 DataType。这里使用稳定的
# CustomData 类身份路由, 确保回测流式回放与 paper Catalog bootstrap 进入同一 Topic。
FACTOR_DATA_TYPE = DataType(FactorScoreData)


@dataclass(frozen=True)
class FactorImportSummary:
    """一次 FactorBatch 导入结果。"""

    delivery_id: str
    rows_received: int
    rows_imported: int
    dates_imported: int
    instruments_imported: int
    already_imported: bool


@dataclass(frozen=True)
class _ExternalFactorRow:
    security_id: str
    symbol: str
    asof_date: date
    score: float | None
    eligible: bool


@dataclass(frozen=True)
class _BundleMetadata:
    delivery_id: str
    source_kind: str
    model_release_id: str
    minimum_asof_date: date
    maximum_asof_date: date


@dataclass(frozen=True)
class _ValidatedBundle:
    metadata: _BundleMetadata
    rows: tuple[_ExternalFactorRow, ...]


def _expect_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} keys mismatch: missing={missing}, extra={extra}")


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _float(value: object, label: str) -> float:
    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite float")
    return value


def _sha256(value: object, label: str) -> str:
    result = _string(value, label)
    if _SHA256_PATTERN.fullmatch(result) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _git_commit(value: object, label: str) -> str:
    result = _string(value, label)
    if _GIT_COMMIT_PATTERN.fullmatch(result) is None:
        raise ValueError(f"{label} must be a 40-character lowercase Git commit")
    return result


def _iso_date(value: object, label: str) -> date:
    text = _string(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _utc_datetime(value: object, label: str) -> datetime:
    text = _string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC")
    return parsed


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_delivery_id(manifest: dict[str, Any]) -> str:
    identity = dict(manifest)
    identity.pop("created_at")
    identity.pop("delivery_id")
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_manifest(bundle_dir: Path, factor_path: Path) -> _BundleMetadata:
    manifest_path = bundle_dir / "manifest.json"
    try:
        decoded = cast(object, json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("FactorBatch manifest.json is unreadable") from exc
    manifest = _mapping(decoded, "manifest")
    _expect_keys(
        manifest,
        {
            "contract",
            "status",
            "delivery_id",
            "created_at",
            "source",
            "model",
            "input",
            "time",
            "coverage",
            "artifact",
        },
        "manifest",
    )
    if manifest["contract"] != FACTOR_CONTRACT:
        raise ValueError(f"manifest.contract must be {FACTOR_CONTRACT!r}")
    if manifest["status"] != "complete":
        raise ValueError("manifest.status must be 'complete'")
    _utc_datetime(manifest["created_at"], "manifest.created_at")

    delivery_id = _sha256(manifest["delivery_id"], "manifest.delivery_id")
    if bundle_dir.name != delivery_id:
        raise ValueError("FactorBatch directory name must equal manifest.delivery_id")

    source = _mapping(manifest["source"], "manifest.source")
    _expect_keys(
        source,
        {"kind", "repository", "commit", "run_id", "run_manifest_sha256"},
        "manifest.source",
    )
    source_kind = _string(source["kind"], "manifest.source.kind")
    if source_kind not in _SOURCE_SEMANTICS:
        raise ValueError("manifest.source.kind is unsupported")
    _string(source["repository"], "manifest.source.repository")
    _git_commit(source["commit"], "manifest.source.commit")
    _string(source["run_id"], "manifest.source.run_id")
    _sha256(source["run_manifest_sha256"], "manifest.source.run_manifest_sha256")

    model = _mapping(manifest["model"], "manifest.model")
    _expect_keys(
        model,
        {
            "release_id",
            "model_id",
            "model_type",
            "checkpoint_sha256",
            "training_dataset_id",
            "higher_score_is_better",
            "forecast_horizon_sessions",
            "score_semantics",
        },
        "manifest.model",
    )
    model_release_id = _sha256(model["release_id"], "manifest.model.release_id")
    _string(model["model_id"], "manifest.model.model_id")
    if model["model_type"] != "financial_pretrained_patchtst":
        raise ValueError("manifest.model.model_type must be financial_pretrained_patchtst")
    _sha256(model["checkpoint_sha256"], "manifest.model.checkpoint_sha256")
    _string(model["training_dataset_id"], "manifest.model.training_dataset_id")
    if model["higher_score_is_better"] is not True:
        raise ValueError("manifest.model.higher_score_is_better must be true")
    _integer(
        model["forecast_horizon_sessions"],
        "manifest.model.forecast_horizon_sessions",
        minimum=1,
    )
    if model["score_semantics"] != "raw_cross_sectional_rank_score":
        raise ValueError("manifest.model.score_semantics is unsupported")

    input_metadata = _mapping(manifest["input"], "manifest.input")
    _expect_keys(
        input_metadata,
        {
            "snapshot_id",
            "snapshot_manifest_sha256",
            "universe_semantics",
            "universe_sha256",
            "identity_policy",
        },
        "manifest.input",
    )
    _string(input_metadata["snapshot_id"], "manifest.input.snapshot_id")
    _sha256(
        input_metadata["snapshot_manifest_sha256"],
        "manifest.input.snapshot_manifest_sha256",
    )
    if input_metadata["universe_semantics"] != _SOURCE_SEMANTICS[source_kind]:
        raise ValueError("manifest.input.universe_semantics does not match source.kind")
    _sha256(input_metadata["universe_sha256"], "manifest.input.universe_sha256")
    if input_metadata["identity_policy"] not in {
        "provider_neutral_security_id",
        "eodhd_isin_only",
    }:
        raise ValueError("manifest.input.identity_policy is unsupported")

    time_metadata = _mapping(manifest["time"], "manifest.time")
    _expect_keys(
        time_metadata,
        {
            "calendar",
            "calendar_version",
            "timezone",
            "minimum_asof_date",
            "maximum_asof_date",
            "signal_available",
            "earliest_execution",
        },
        "manifest.time",
    )
    if time_metadata["calendar"] != "US_EQUITIES_REGULAR":
        raise ValueError("manifest.time.calendar must be US_EQUITIES_REGULAR")
    _string(time_metadata["calendar_version"], "manifest.time.calendar_version")
    if time_metadata["timezone"] != "America/New_York":
        raise ValueError("manifest.time.timezone must be America/New_York")
    minimum_asof_date = _iso_date(
        time_metadata["minimum_asof_date"], "manifest.time.minimum_asof_date"
    )
    maximum_asof_date = _iso_date(
        time_metadata["maximum_asof_date"], "manifest.time.maximum_asof_date"
    )
    if minimum_asof_date > maximum_asof_date:
        raise ValueError("manifest.time minimum date is after maximum date")
    if source_kind == "signal_inference" and minimum_asof_date != maximum_asof_date:
        raise ValueError("signal_inference must contain exactly one as-of date")
    if time_metadata["signal_available"] != "after_regular_session_close":
        raise ValueError("manifest.time.signal_available is unsupported")
    if time_metadata["earliest_execution"] != "next_regular_session_open":
        raise ValueError("manifest.time.earliest_execution is unsupported")

    coverage = _mapping(manifest["coverage"], "manifest.coverage")
    _expect_keys(
        coverage,
        {
            "candidate_rows",
            "actual_rows",
            "expected_eligible_rows",
            "scored_eligible_rows",
            "missing_eligible_rows",
            "ratio",
        },
        "manifest.coverage",
    )
    candidate_rows = _integer(
        coverage["candidate_rows"], "manifest.coverage.candidate_rows", minimum=1
    )
    actual_rows = _integer(coverage["actual_rows"], "manifest.coverage.actual_rows", minimum=1)
    expected_eligible_rows = _integer(
        coverage["expected_eligible_rows"],
        "manifest.coverage.expected_eligible_rows",
    )
    scored_eligible_rows = _integer(
        coverage["scored_eligible_rows"],
        "manifest.coverage.scored_eligible_rows",
    )
    missing_eligible_rows = _integer(
        coverage["missing_eligible_rows"],
        "manifest.coverage.missing_eligible_rows",
    )
    ratio = _float(coverage["ratio"], "manifest.coverage.ratio")
    if (
        candidate_rows != actual_rows
        or expected_eligible_rows != scored_eligible_rows
        or expected_eligible_rows > candidate_rows
        or missing_eligible_rows != 0
        or ratio != 1.0
    ):
        raise ValueError("FactorBatch coverage must be complete")
    if source_kind == "evaluation_predictions" and candidate_rows != expected_eligible_rows:
        raise ValueError("evaluation_predictions may contain only eligible scored rows")

    artifact = _mapping(manifest["artifact"], "manifest.artifact")
    _expect_keys(
        artifact,
        {"file", "sha256", "bytes", "row_count", "date_count"},
        "manifest.artifact",
    )
    if artifact["file"] != "factors.parquet":
        raise ValueError("manifest.artifact.file must be factors.parquet")
    factor_hash = _file_sha256(factor_path)
    artifact_hash = _sha256(artifact["sha256"], "manifest.artifact.sha256")
    if artifact_hash != factor_hash:
        raise ValueError("FactorBatch factors hash does not match manifest")
    artifact_bytes = _integer(artifact["bytes"], "manifest.artifact.bytes", minimum=1)
    if artifact_bytes != factor_path.stat().st_size:
        raise ValueError("manifest.artifact.bytes does not match factors.parquet")
    row_count = _integer(artifact["row_count"], "manifest.artifact.row_count", minimum=1)
    _integer(artifact["date_count"], "manifest.artifact.date_count", minimum=1)
    if row_count != actual_rows:
        raise ValueError("manifest row counts disagree")
    if delivery_id != _semantic_delivery_id(manifest):
        raise ValueError("FactorBatch semantic identity does not match delivery_id")

    return _BundleMetadata(
        delivery_id=delivery_id,
        source_kind=source_kind,
        model_release_id=model_release_id,
        minimum_asof_date=minimum_asof_date,
        maximum_asof_date=maximum_asof_date,
    )


def _load_rows(
    factor_path: Path,
    metadata: _BundleMetadata,
    manifest_path: Path,
) -> tuple[_ExternalFactorRow, ...]:
    try:
        frame = pd.read_parquet(factor_path, dtype_backend="pyarrow")
    except Exception as exc:
        raise ValueError("FactorBatch factors.parquet is unreadable") from exc
    if tuple(frame.columns) != FACTOR_COLUMNS:
        raise ValueError(
            f"factors.parquet columns must be exactly {list(FACTOR_COLUMNS)}; "
            f"received {list(frame.columns)}"
        )
    actual_types = tuple(str(value) for value in frame.dtypes)
    expected_types = (
        frozenset({"string[pyarrow]", "large_string[pyarrow]"}),
        frozenset({"string[pyarrow]", "large_string[pyarrow]"}),
        frozenset({"date32[day][pyarrow]"}),
        frozenset({"double[pyarrow]"}),
        frozenset({"bool[pyarrow]"}),
    )
    type_pairs = zip(actual_types, expected_types, strict=True)
    if any(actual not in expected for actual, expected in type_pairs):
        raise ValueError(
            "factors.parquet schema mismatch: expected string/string/date32/float64/bool, "
            f"received={actual_types}"
        )
    if frame.empty:
        raise ValueError("factors.parquet must not be empty")
    rows: list[_ExternalFactorRow] = []
    keys: list[tuple[date, str]] = []
    for index, values in enumerate(frame.itertuples(index=False, name=None)):
        security_id, symbol, asof_value, score_value, eligible_value = values
        row_label = f"factors.parquet row {index}"
        security = _string(security_id, f"{row_label}.security_id")
        display_symbol = _string(symbol, f"{row_label}.symbol")
        if isinstance(asof_value, datetime) or not isinstance(asof_value, date):
            raise ValueError(f"{row_label}.asof_date must use Parquet date type")
        if not isinstance(eligible_value, bool):
            raise ValueError(f"{row_label}.eligible must be boolean")
        score: float | None
        if pd.isna(score_value):
            score = None
        elif isinstance(score_value, bool) or not isinstance(score_value, (int, float)):
            raise ValueError(f"{row_label}.score must be numeric or null")
        else:
            score = float(score_value)
            if not math.isfinite(score):
                raise ValueError(f"{row_label}.score must be finite")
        if eligible_value and score is None:
            raise ValueError(f"{row_label}.score is required when eligible is true")
        if not eligible_value and score is not None:
            raise ValueError(f"{row_label}.score must be null when eligible is false")
        keys.append((asof_value, security))
        rows.append(
            _ExternalFactorRow(
                security_id=security,
                symbol=display_symbol,
                asof_date=asof_value,
                score=score,
                eligible=eligible_value,
            )
        )
    if keys != sorted(keys):
        raise ValueError("factors.parquet must be sorted by asof_date and security_id")
    if len(keys) != len(set(keys)):
        raise ValueError("factors.parquet contains duplicate security_id/asof_date keys")

    manifest = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    artifact = cast(dict[str, Any], manifest["artifact"])
    coverage = cast(dict[str, Any], manifest["coverage"])
    input_metadata = cast(dict[str, Any], manifest["input"])
    unique_dates = {row.asof_date for row in rows}
    if len(rows) != int(artifact["row_count"]) or len(rows) != int(coverage["actual_rows"]):
        raise ValueError("manifest row count does not match factors.parquet")
    if len(unique_dates) != int(artifact["date_count"]):
        raise ValueError("manifest date count does not match factors.parquet")
    if (
        min(unique_dates) != metadata.minimum_asof_date
        or max(unique_dates) != metadata.maximum_asof_date
    ):
        raise ValueError("manifest date range does not match factors.parquet")
    eligible_rows = sum(row.eligible for row in rows)
    if (
        int(coverage["candidate_rows"]) != len(rows)
        or int(coverage["expected_eligible_rows"]) != eligible_rows
        or int(coverage["scored_eligible_rows"]) != eligible_rows
    ):
        raise ValueError("manifest coverage does not match factors.parquet")
    universe_digest = hashlib.sha256()
    for row in rows:
        line = json.dumps(
            [row.security_id, row.symbol, row.asof_date.isoformat(), row.eligible],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        universe_digest.update(line.encode("utf-8"))
        universe_digest.update(b"\n")
    if input_metadata["universe_sha256"] != universe_digest.hexdigest():
        raise ValueError("FactorBatch universe hash does not match factors.parquet")
    return tuple(rows)


def _validate_bundle(bundle_dir: Path) -> _ValidatedBundle:
    resolved = bundle_dir.expanduser().resolve()
    if not resolved.is_dir() or resolved.name.startswith("."):
        raise ValueError("FactorBatch must be a finalized directory")
    entries = {path.name for path in resolved.iterdir()}
    if entries != {"factors.parquet", "manifest.json"}:
        raise ValueError(
            "FactorBatch directory must contain only factors.parquet and manifest.json"
        )
    factor_path = resolved / "factors.parquet"
    manifest_path = resolved / "manifest.json"
    metadata = _load_manifest(resolved, factor_path)
    return _ValidatedBundle(
        metadata=metadata,
        rows=_load_rows(factor_path, metadata, manifest_path),
    )


def _timestamp_bounds(first: date, last: date) -> tuple[int, int]:
    start = datetime.combine(first, time.min, tzinfo=UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=UTC)
    return int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000)


def _bar_availability_by_date(
    catalog: CatalogRepository,
    spec: InstrumentSpec,
    suffix: str,
    first: date,
    last: date,
) -> dict[date, int]:
    start_ns, end_ns = _timestamp_bounds(first, last)
    bar_type = BarType.from_str(f"{spec.canonical_id}-{suffix}")
    bars = catalog.read_bars(bar_type, start_ns=start_ns, end_ns=end_ns)
    result: dict[date, int] = {}
    for bar in bars:
        event_date = datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=UTC).date()
        if event_date in result:
            raise ValueError(f"duplicate {suffix} bars for {spec.canonical_id} on {event_date}")
        result[event_date] = bar.ts_init
    return result


def _score_signature(value: FactorScoreData) -> tuple[object, ...]:
    return (
        value.canonical_id,
        value.security_id,
        value.asof_date,
        value.score,
        value.eligible,
        value.batch_id,
        value.batch_size,
        value.delivery_id,
        value.model_release_id,
        value.source_kind,
        value.ts_event,
        value.ts_init,
    )


def _catalog_factor_scores(catalog: CatalogRepository) -> tuple[FactorScoreData, ...]:
    values = catalog.catalog.query(FactorScoreData)
    result: list[FactorScoreData] = []
    for value in values:
        payload = value.data if isinstance(value, CustomData) else value
        if not isinstance(payload, FactorScoreData):
            raise TypeError("NT Catalog returned an unexpected factor data type")
        result.append(payload)
    return tuple(result)


def import_factor_bundle(
    *,
    bundle_dir: Path,
    catalog_path: Path,
    instruments: tuple[InstrumentSpec, ...],
    signal_bar_type_suffix: str,
    execution_bar_type_suffix: str,
) -> FactorImportSummary:
    """完整校验一个 FactorBatch 并原子地写入规范 NT Catalog。"""
    bundle = _validate_bundle(bundle_dir)
    mapped = [spec for spec in instruments if spec.factor_security_id is not None]
    if not mapped:
        raise ValueError("No instruments define factor_security_id")
    factor_ids = [cast(str, spec.factor_security_id) for spec in mapped]
    if len(factor_ids) != len(set(factor_ids)):
        raise ValueError("factor_security_id mappings must be unique")
    rows_by_key = {(row.asof_date, row.security_id): row for row in bundle.rows}
    factor_dates = sorted({row.asof_date for row in bundle.rows})
    catalog = CatalogRepository(catalog_path)
    signal_availability = {
        spec.canonical_id: _bar_availability_by_date(
            catalog,
            spec,
            signal_bar_type_suffix,
            bundle.metadata.minimum_asof_date,
            bundle.metadata.maximum_asof_date,
        )
        for spec in mapped
    }
    execution_availability = {
        spec.canonical_id: _bar_availability_by_date(
            catalog,
            spec,
            execution_bar_type_suffix,
            bundle.metadata.minimum_asof_date,
            bundle.metadata.maximum_asof_date,
        )
        for spec in mapped
    }

    scores: list[FactorScoreData] = []
    for asof_date in factor_dates:
        active_specs = [
            spec
            for spec in mapped
            if spec.effective_trading_interval(asof_date, asof_date) is not None
        ]
        if not active_specs:
            continue
        selected: list[tuple[InstrumentSpec, _ExternalFactorRow]] = []
        for spec in active_specs:
            security_id = cast(str, spec.factor_security_id)
            row = rows_by_key.get((asof_date, security_id))
            if row is None:
                if bundle.metadata.source_kind == "signal_inference":
                    raise ValueError(
                        f"complete factor cross-section is missing {security_id} on {asof_date}"
                    )
                row = _ExternalFactorRow(
                    security_id=security_id,
                    symbol=spec.symbol,
                    asof_date=asof_date,
                    score=None,
                    eligible=False,
                )
            selected.append((spec, row))

        signal_available_times = [
            signal_availability[spec.canonical_id][asof_date]
            for spec, _ in selected
            if asof_date in signal_availability[spec.canonical_id]
        ]
        if not signal_available_times:
            raise ValueError(f"Catalog has no signal bar for factor date {asof_date}")
        for spec, row in selected:
            if row.eligible and asof_date not in signal_availability[spec.canonical_id]:
                raise ValueError(
                    f"Catalog has no signal bar for eligible {spec.canonical_id} on {asof_date}"
                )
            if row.eligible and asof_date not in execution_availability[spec.canonical_id]:
                raise ValueError(
                    f"Catalog has no execution bar for eligible {spec.canonical_id} on {asof_date}"
                )
        available_at_ns = max(signal_available_times)
        batch_id = f"{bundle.metadata.delivery_id}:{asof_date.isoformat()}"
        for spec, row in selected:
            scores.append(
                FactorScoreData(
                    canonical_id=spec.canonical_id,
                    security_id=row.security_id,
                    asof_date=asof_date.isoformat(),
                    score=0.0 if row.score is None else row.score,
                    eligible=row.eligible,
                    batch_id=batch_id,
                    batch_size=len(selected),
                    delivery_id=bundle.metadata.delivery_id,
                    model_release_id=bundle.metadata.model_release_id,
                    source_kind=bundle.metadata.source_kind,
                    ts_event=available_at_ns,
                    ts_init=available_at_ns,
                )
            )
    if not scores:
        raise ValueError("FactorBatch has no rows mapped to active configured instruments")
    scores.sort(key=lambda value: (value.ts_init, value.canonical_id))

    existing = _catalog_factor_scores(catalog)
    existing_by_key = {(value.asof_date, value.canonical_id): value for value in existing}
    overlapping = [
        value for value in scores if (value.asof_date, value.canonical_id) in existing_by_key
    ]
    if overlapping:
        if len(overlapping) == len(scores) and all(
            _score_signature(value)
            == _score_signature(existing_by_key[(value.asof_date, value.canonical_id)])
            for value in scores
        ):
            return FactorImportSummary(
                delivery_id=bundle.metadata.delivery_id,
                rows_received=len(bundle.rows),
                rows_imported=0,
                dates_imported=0,
                instruments_imported=0,
                already_imported=True,
            )
        raise ValueError("Factor Catalog already contains conflicting rows for this date range")

    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, value) for value in scores])
    return FactorImportSummary(
        delivery_id=bundle.metadata.delivery_id,
        rows_received=len(bundle.rows),
        rows_imported=len(scores),
        dates_imported=len({value.asof_date for value in scores}),
        instruments_imported=len({value.canonical_id for value in scores}),
        already_imported=False,
    )
