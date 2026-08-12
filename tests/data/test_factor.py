"""FactorBatch 契约校验与 NT Catalog 导入测试。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from tests.data.helpers import make_bar
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.data.factor import (
    FACTOR_DATA_METADATA,
    FactorScoreData,
    _mapping,
    _number,
    _string,
    import_factor_bundle,
)


def _spec(
    symbol: str,
    security_id: str,
    *,
    first_trading_date: date | None = None,
) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        instrument_id=f"{symbol}.US",
        data_symbol=f"{symbol}.US",
        exchange="SMART",
        primary_exchange="NASDAQ",
        currency="USD",
        price_precision=2,
        price_increment="0.01",
        lot_size=1,
        live_instrument_id=f"{symbol}.NASDAQ",
        first_trading_date=first_trading_date,
        factor_security_id=security_id,
    )


def _rows() -> list[dict[str, object]]:
    return [
        {
            "security_id": "eodhd:isin:AAPL",
            "symbol": "AAPL",
            "asof_date": date(2025, 1, 2),
            "score": 2.0,
            "eligible": True,
        },
        {
            "security_id": "eodhd:isin:MSFT",
            "symbol": "MSFT",
            "asof_date": date(2025, 1, 2),
            "score": 1.0,
            "eligible": True,
        },
        {
            "security_id": "eodhd:isin:AAPL",
            "symbol": "AAPL",
            "asof_date": date(2025, 1, 3),
            "score": 3.0,
            "eligible": True,
        },
        {
            "security_id": "eodhd:isin:MSFT",
            "symbol": "MSFT",
            "asof_date": date(2025, 1, 3),
            "score": None,
            "eligible": False,
        },
    ]


def _write_bundle(
    root: Path,
    *,
    rows: list[dict[str, object]] | None = None,
    source_kind: str = "signal_inference",
    mutate_manifest: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    staging = root / ".staging"
    staging.mkdir(parents=True)
    values = rows or _rows()
    frame = pd.DataFrame(values)
    factor_path = staging / "factors.parquet"
    frame.to_parquet(factor_path, index=False)
    digest = hashlib.sha256(factor_path.read_bytes()).hexdigest()
    dates = sorted({str(value["asof_date"]) for value in values})
    semantics = (
        "complete_candidate_cross_section"
        if source_kind == "signal_inference"
        else "eligible_scored_cross_section"
    )
    manifest: dict[str, object] = {
        "contract": "facdigger.factor_batch",
        "status": "complete",
        "delivery_id": digest,
        "created_at": "2025-01-04T00:00:00+00:00",
        "source": {
            "kind": source_kind,
            "repository": "wwbotww/FacDiggerNN",
            "commit": "abc123",
            "run_id": "e3-final",
            "run_manifest_sha256": "1" * 64,
        },
        "model": {
            "release_id": "2" * 64,
            "model_id": "e3_rankq",
            "model_type": "financial_pretrained_patchtst",
            "checkpoint_sha256": "3" * 64,
            "training_dataset_id": "train-snapshot",
            "higher_score_is_better": True,
            "forecast_horizon_sessions": 5,
        },
        "input": {
            "snapshot_id": "daily-snapshot",
            "universe_semantics": semantics,
        },
        "time": {
            "calendar": "US_EQUITIES_REGULAR",
            "timezone": "America/New_York",
            "minimum_asof_date": dates[0],
            "maximum_asof_date": dates[-1],
            "signal_available": "after_regular_session_close",
            "earliest_execution": "next_regular_session_open",
        },
        "coverage": {
            "expected_rows": len(values),
            "actual_rows": len(values),
            "ratio": 1.0,
        },
        "artifact": {
            "file": "factors.parquet",
            "sha256": digest,
            "bytes": factor_path.stat().st_size,
            "row_count": len(values),
            "date_count": len(dates),
        },
    }
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    delivery_id = str(manifest["delivery_id"])
    bundle = root / delivery_id
    staging.rename(bundle)
    return bundle


def _write_signal_bars(catalog_path: Path) -> None:
    catalog = CatalogRepository(catalog_path)
    bars = [
        make_bar(
            session,
            instrument_id=f"{symbol}.US",
            bar_type_suffix="1-DAY-LAST-INTERNAL",
        )
        for session in (date(2025, 1, 2), date(2025, 1, 3))
        for symbol in ("AAPL", "MSFT")
    ]
    assert catalog.append_new_bars(bars) == 4


def _scores(catalog_path: Path) -> list[FactorScoreData]:
    catalog = CatalogRepository(catalog_path)
    values = catalog.catalog.query(FactorScoreData, metadata=FACTOR_DATA_METADATA)
    return [cast(FactorScoreData, value.data) for value in values]


def test_factor_bundle_imports_nt_custom_data_and_is_idempotent(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path / "bundles")
    catalog_path = tmp_path / "catalog"
    _write_signal_bars(catalog_path)
    instruments = (
        _spec("AAPL", "eodhd:isin:AAPL"),
        _spec("MSFT", "eodhd:isin:MSFT"),
    )

    summary = import_factor_bundle(
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
    )

    assert summary.rows_received == 4
    assert summary.rows_imported == 4
    assert summary.dates_imported == 2
    assert summary.instruments_imported == 2
    scores = _scores(catalog_path)
    assert len(scores) == 4
    assert {score.batch_size for score in scores} == {2}
    assert all(score.delivery_id == summary.delivery_id for score in scores)
    ineligible = next(score for score in scores if not score.eligible)
    assert ineligible.canonical_id == "MSFT.US"
    assert ineligible.score == 0.0

    repeated = import_factor_bundle(
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
    )
    assert repeated.already_imported
    assert repeated.rows_imported == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest.update({"schema_version": 1}), "keys mismatch"),
        (
            lambda manifest: cast(dict[str, object], manifest["coverage"]).update({"ratio": 0.5}),
            "coverage",
        ),
        (lambda manifest: manifest.update({"delivery_id": "0" * 64}), "content hash"),
        (
            lambda manifest: cast(dict[str, object], manifest["model"]).update(
                {"higher_score_is_better": False}
            ),
            "higher_score_is_better",
        ),
    ],
)
def test_factor_bundle_rejects_invalid_manifest(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
    message: str,
) -> None:
    bundle = _write_bundle(tmp_path, mutate_manifest=mutation)
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest.update({"contract": "wrong"}), "contract"),
        (lambda manifest: manifest.update({"status": "writing"}), "status"),
        (lambda manifest: manifest.update({"created_at": "not-a-date"}), "datetime"),
        (
            lambda manifest: cast(dict[str, object], manifest["source"]).update(
                {"kind": "unknown"}
            ),
            "source.kind",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["model"]).update(
                {"model_type": "other"}
            ),
            "model_type",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["input"]).update(
                {"universe_semantics": "wrong"}
            ),
            "universe_semantics",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["time"]).update({"calendar": "UTC"}),
            "calendar",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["time"]).update({"timezone": "UTC"}),
            "timezone",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["time"]).update(
                {"signal_available": "before_open"}
            ),
            "signal_available",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["time"]).update(
                {"earliest_execution": "same_close"}
            ),
            "earliest_execution",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["artifact"]).update(
                {"file": "predictions.parquet"}
            ),
            "artifact.file",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["artifact"]).update({"bytes": 1}),
            "artifact.bytes",
        ),
    ],
)
def test_factor_bundle_rejects_contract_semantic_changes(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
    message: str,
) -> None:
    bundle = _write_bundle(tmp_path, mutate_manifest=mutation)
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )


def test_factor_bundle_rejects_unfinalized_or_malformed_delivery(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="finalized"):
        import_factor_bundle(
            bundle_dir=missing,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )

    bundle = _write_bundle(tmp_path / "visible")
    (bundle / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="only"):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )

    unreadable = _write_bundle(tmp_path / "manifest")
    (unreadable / "manifest.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        import_factor_bundle(
            bundle_dir=unreadable,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )


def test_factor_contract_scalar_validators_fail_closed() -> None:
    with pytest.raises(ValueError, match="object"):
        _mapping([], "field")
    with pytest.raises(ValueError, match="non-empty"):
        _string("", "field")
    with pytest.raises(ValueError, match="numeric"):
        _number(True, "field")
    with pytest.raises(ValueError, match="finite"):
        _number(float("inf"), "field")


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": date(2025, 1, 2),
                    "score": None,
                    "eligible": True,
                }
            ],
            "score is required",
        ),
        (
            [
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": date(2025, 1, 2),
                    "score": 1.0,
                    "eligible": True,
                    "target": 0.1,
                }
            ],
            "columns",
        ),
        (
            [
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": date(2025, 1, 2),
                    "score": 1.0,
                    "eligible": True,
                },
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": date(2025, 1, 2),
                    "score": 2.0,
                    "eligible": True,
                },
            ],
            "duplicate",
        ),
        (
            [
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": date(2025, 1, 3),
                    "score": 1.0,
                    "eligible": True,
                },
                {
                    "security_id": "eodhd:isin:MSFT",
                    "symbol": "MSFT",
                    "asof_date": date(2025, 1, 2),
                    "score": 2.0,
                    "eligible": True,
                },
            ],
            "sorted",
        ),
        (
            [
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": date(2025, 1, 2),
                    "score": 1.0,
                    "eligible": False,
                }
            ],
            "score must be null",
        ),
        (
            [
                {
                    "security_id": "eodhd:isin:AAPL",
                    "symbol": "AAPL",
                    "asof_date": "2025-01-02",
                    "score": 1.0,
                    "eligible": True,
                }
            ],
            "Parquet date type",
        ),
    ],
)
def test_factor_bundle_rejects_invalid_rows(
    tmp_path: Path,
    rows: list[dict[str, object]],
    message: str,
) -> None:
    bundle = _write_bundle(tmp_path, rows=rows)
    _write_signal_bars(tmp_path / "catalog")
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )


def test_factor_import_requires_prices_and_complete_production_cross_section(
    tmp_path: Path,
) -> None:
    missing_row = [row for row in _rows() if row["security_id"] != "eodhd:isin:MSFT"]
    bundle = _write_bundle(tmp_path / "production", rows=missing_row)
    catalog_path = tmp_path / "catalog"
    _write_signal_bars(catalog_path)
    instruments = (
        _spec("AAPL", "eodhd:isin:AAPL"),
        _spec("MSFT", "eodhd:isin:MSFT"),
    )
    with pytest.raises(ValueError, match="complete factor cross-section"):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=instruments,
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )

    evaluation = _write_bundle(
        tmp_path / "evaluation",
        rows=missing_row,
        source_kind="evaluation_predictions",
    )
    _write_signal_bars(tmp_path / "evaluation-catalog")
    summary = import_factor_bundle(
        bundle_dir=evaluation,
        catalog_path=tmp_path / "evaluation-catalog",
        instruments=(_spec("MSFT", "eodhd:isin:MSFT"),),
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
    )
    assert summary.rows_imported == 2
    assert all(not score.eligible for score in _scores(tmp_path / "evaluation-catalog"))


def test_factor_import_rejects_missing_signal_bar_for_eligible_row(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path / "bundles")
    with pytest.raises(ValueError, match="no signal bar"):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )


def test_factor_import_rejects_missing_mapping_lifecycle_and_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _write_bundle(tmp_path / "bundles")
    catalog_path = tmp_path / "catalog"
    _write_signal_bars(catalog_path)
    without_mapping = _spec("AAPL", "eodhd:isin:AAPL")
    without_mapping = replace(without_mapping, factor_security_id=None)
    with pytest.raises(ValueError, match="No instruments"):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(without_mapping,),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )

    inactive = _spec(
        "AAPL",
        "eodhd:isin:AAPL",
        first_trading_date=date(2026, 1, 1),
    )
    with pytest.raises(ValueError, match="no rows mapped"):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(inactive,),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )

    instruments = (
        _spec("AAPL", "eodhd:isin:AAPL"),
        _spec("MSFT", "eodhd:isin:MSFT"),
    )
    import_factor_bundle(
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
    )
    changed = _scores(catalog_path)[0]
    replacement = FactorScoreData(
        canonical_id=changed.canonical_id,
        security_id=changed.security_id,
        asof_date=changed.asof_date,
        score=changed.score + 1.0,
        eligible=changed.eligible,
        batch_id=changed.batch_id,
        batch_size=changed.batch_size,
        delivery_id=changed.delivery_id,
        model_release_id=changed.model_release_id,
        source_kind=changed.source_kind,
        ts_event=changed.ts_event,
        ts_init=changed.ts_init,
    )
    monkeypatch.setattr(
        "trading_assistant.data.factor._catalog_factor_scores",
        lambda _: (replacement,),
    )
    with pytest.raises(ValueError, match="conflicting rows"):
        import_factor_bundle(
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=instruments,
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        )
