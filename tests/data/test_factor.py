"""FactorBatch 契约校验与 NT Catalog 导入测试。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pandas as pd
import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.common.data_topics import TopicCache
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.data.messages import RequestData
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.identifiers import ClientId, TraderId

from tests.data.helpers import make_bar
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import FactorIdentityPeriod, InstrumentSpec, load_instruments
from trading_assistant.data.factor import (
    FACTOR_DATA_TYPE,
    FactorScoreData,
    _float,
    _git_commit,
    _mapping,
    _number,
    _semantic_delivery_id,
    _string,
    import_factor_bundle,
)
from trading_assistant.data.market_calendar import CALENDAR_VERSION
from trading_assistant.storage.repository import TradingRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FACDIGGER_MOCK_DELIVERY_ID = "577949642a10b1cdbf8bab569c306f38838c58df203ba0bafb849ba3eda2798b"


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
            "score": None,
            "eligible": False,
        },
    ]


def _dated_spec() -> InstrumentSpec:
    """合成两个交易日的身份切换, 不代表真实证券变更。"""
    return replace(
        _spec("AAPL", "eodhd:isin:AAPL"),
        factor_security_id=None,
        factor_identity_periods=(
            FactorIdentityPeriod(
                "eodhd:isin:AAPL", date(2025, 1, 2), date(2025, 1, 2), "合成旧身份"
            ),
            FactorIdentityPeriod(
                "eodhd:isin:AAPL_NEW", date(2025, 1, 3), date(2025, 1, 3), "合成新身份"
            ),
        ),
    )


def _write_bundle(
    root: Path,
    *,
    rows: list[dict[str, object]] | None = None,
    source_kind: str = "signal_inference",
    score_dtype: str = "float64",
    mutate_manifest: Callable[[dict[str, object]], None] | None = None,
    preserve_delivery_id: bool = False,
) -> Path:
    staging = root / ".staging"
    staging.mkdir(parents=True)
    values = rows or _rows()
    frame = pd.DataFrame(values).astype(
        {
            "security_id": "string",
            "symbol": "string",
            "score": score_dtype,
            "eligible": "bool",
        }
    )
    factor_path = staging / "factors.parquet"
    frame.to_parquet(factor_path, index=False)
    digest = hashlib.sha256(factor_path.read_bytes()).hexdigest()
    dates = sorted({str(value["asof_date"]) for value in values})
    eligible_rows = sum(value["eligible"] is True for value in values)
    universe_digest = hashlib.sha256()
    for value in values:
        line = json.dumps(
            [
                value["security_id"],
                value["symbol"],
                str(value["asof_date"]),
                value["eligible"],
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        universe_digest.update(line.encode("utf-8"))
        universe_digest.update(b"\n")
    semantics = (
        "complete_candidate_cross_section"
        if source_kind == "signal_inference"
        else "eligible_scored_cross_section"
    )
    manifest: dict[str, object] = {
        "contract": "facdigger.factor_batch",
        "status": "complete",
        "delivery_id": "0" * 64,
        "created_at": "2025-01-04T00:00:00+00:00",
        "source": {
            "kind": source_kind,
            "repository": "wwbotww/FacDiggerNN",
            "commit": "a" * 40,
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
            "score_semantics": "raw_cross_sectional_rank_score",
        },
        "input": {
            "snapshot_id": "daily-snapshot",
            "snapshot_manifest_sha256": "4" * 64,
            "universe_semantics": semantics,
            "universe_sha256": universe_digest.hexdigest(),
            "identity_policy": "eodhd_isin_only",
        },
        "time": {
            "calendar": "US_EQUITIES_REGULAR",
            "calendar_version": CALENDAR_VERSION,
            "timezone": "America/New_York",
            "minimum_asof_date": dates[0],
            "maximum_asof_date": dates[-1],
            "signal_available": "after_regular_session_close",
            "earliest_execution": "next_regular_session_open",
        },
        "coverage": {
            "candidate_rows": len(values),
            "actual_rows": len(values),
            "expected_eligible_rows": eligible_rows,
            "scored_eligible_rows": eligible_rows,
            "missing_eligible_rows": 0,
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
    original_delivery_id = _semantic_delivery_id(manifest)
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    manifest["delivery_id"] = (
        original_delivery_id if preserve_delivery_id else _semantic_delivery_id(manifest)
    )
    (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    delivery_id = str(manifest["delivery_id"])
    bundle = root / delivery_id
    staging.rename(bundle)
    return bundle


def _write_price_bars(catalog_path: Path) -> None:
    catalog = CatalogRepository(catalog_path)
    bars = [
        make_bar(
            session,
            instrument_id=f"{symbol}.US",
            bar_type_suffix=bar_type_suffix,
        )
        for session in (date(2025, 1, 2), date(2025, 1, 3))
        for symbol in ("AAPL", "MSFT")
        for bar_type_suffix in (
            "1-DAY-LAST-INTERNAL",
            "1-DAY-LAST-EXTERNAL",
        )
    ]
    assert catalog.append_new_bars(bars) == 8


def _scores(catalog_path: Path) -> list[FactorScoreData]:
    catalog = CatalogRepository(catalog_path)
    values = catalog.catalog.query(FactorScoreData)
    return [cast(FactorScoreData, value.data) for value in values]


@pytest.mark.parametrize("source_kind", ["signal_inference", "evaluation_predictions"])
@pytest.mark.parametrize(
    "model_type", ["financial_pretrained_patchtst", "finance_patch_transformer"]
)
def test_factor_import_resolves_identity_by_asof_date(
    tmp_path: Path, source_kind: str, model_type: str, factor_repository: TradingRepository
) -> None:
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    instrument = _dated_spec()
    rows = [
        {**_rows()[0], "asof_date": session, "security_id": identity, "score": score}
        for session, identity, score in (
            (date(2025, 1, 2), "eodhd:isin:AAPL", 3.25),
            (date(2025, 1, 3), "eodhd:isin:AAPL_NEW", -1.75),
        )
    ]

    def metadata(manifest: dict[str, object]) -> None:
        cast(dict[str, object], manifest["model"])["model_type"] = model_type

    groups = [[row] for row in rows] if source_kind == "signal_inference" else [rows]
    for index, group in enumerate(groups):
        bundle = _write_bundle(
            tmp_path / f"bundle-{index}",
            rows=group,
            source_kind=source_kind,
            mutate_manifest=metadata,
        )
        for already_imported in (False, True):
            result = import_factor_bundle(
                mode="historical",
                repository=factor_repository,
                bundle_dir=bundle,
                catalog_path=catalog_path,
                instruments=(instrument,),
                signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
                execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
            )
            assert result.already_imported is already_imported
            assert result.rows_imported == (0 if already_imported else len(group))
    imported = _scores(catalog_path)
    assert [row.canonical_id for row in imported] == ["AAPL.US", "AAPL.US"]
    assert [row.security_id for row in imported] == [row["security_id"] for row in rows]
    assert [row.score for row in imported] == [3.25, -1.75]
    assert [row.batch_size for row in imported] == [1, 1]
    assert all(row.eligible and row.source_kind == source_kind for row in imported)
    assert imported[0].ts_init < imported[1].ts_init


@pytest.mark.parametrize("source_kind", ["signal_inference", "evaluation_predictions"])
@pytest.mark.parametrize("include_correct", [False, True])
@pytest.mark.parametrize("session", [date(2025, 1, 2), date(2025, 1, 3)])
def test_factor_import_rejects_wrong_date_identity_before_writing(
    tmp_path: Path,
    source_kind: str,
    include_correct: bool,
    session: date,
    factor_repository: TradingRepository,
) -> None:
    instrument = _dated_spec()
    expected = instrument.factor_security_id_on(session)
    wrong = "eodhd:isin:AAPL_NEW" if session.day == 2 else "eodhd:isin:AAPL"
    rows = [
        {**_rows()[0], "asof_date": session, "security_id": wrong},
        {**_rows()[1], "asof_date": session, "score": 0.5, "eligible": True},
    ]
    if include_correct:
        rows.append({**_rows()[0], "asof_date": session, "security_id": expected})
    rows.sort(key=lambda row: str(row["security_id"]))
    bundle = _write_bundle(tmp_path / "bundle", rows=rows, source_kind=source_kind)
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    with pytest.raises(ValueError, match=r"factor identity.*not valid.*2025-01-0"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(instrument, _spec("MSFT", "eodhd:isin:MSFT")),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )
    assert _scores(catalog_path) == []


@pytest.mark.parametrize("source_kind", ["signal_inference", "evaluation_predictions"])
@pytest.mark.parametrize("missing_date", [date(2025, 1, 2), date(2025, 1, 6)])
def test_factor_import_rejects_identity_gap_instead_of_evaluation_placeholder(
    tmp_path: Path, source_kind: str, missing_date: date, factor_repository: TradingRepository
) -> None:
    instrument = replace(
        _dated_spec(), factor_identity_periods=_dated_spec().factor_identity_periods[1:]
    )
    row = {**_rows()[1], "asof_date": missing_date, "score": 0.5, "eligible": True}
    bundle = _write_bundle(tmp_path / "bundle", rows=[row], source_kind=source_kind)
    with pytest.raises(ValueError, match=rf"No factor identity.*AAPL\.US.*{missing_date}"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(instrument, _spec("MSFT", "eodhd:isin:MSFT")),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )
    assert _scores(tmp_path / "catalog") == []


@pytest.mark.parametrize("source_kind", ["signal_inference", "evaluation_predictions"])
def test_dated_mapping_keeps_existing_missing_prediction_rules(
    tmp_path: Path, source_kind: str, factor_repository: TradingRepository
) -> None:
    rows = [{**_rows()[1], "score": 0.5, "eligible": True}]
    bundle = _write_bundle(tmp_path / "bundle", rows=rows, source_kind=source_kind)
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    with (
        pytest.raises(ValueError, match="complete factor cross-section is missing")
        if source_kind == "signal_inference"
        else nullcontext()
    ):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(_dated_spec(), _spec("MSFT", "eodhd:isin:MSFT")),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )
    if source_kind == "signal_inference":
        assert _scores(catalog_path) == []
    else:
        aapl, msft = sorted(_scores(catalog_path), key=lambda row: row.canonical_id)
        assert not aapl.eligible
        assert aapl.security_id == "eodhd:isin:AAPL"
        assert msft.eligible
        assert aapl.batch_size == msft.batch_size == 2


@pytest.mark.parametrize("duplicate_canonical", [False, True])
def test_factor_import_validates_direct_api_mapping_collisions(
    tmp_path: Path, duplicate_canonical: bool, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(tmp_path / "bundle")
    other = (
        _spec("AAPL", "eodhd:isin:UNRELATED")
        if duplicate_canonical
        else _spec("MSFT", "eodhd:isin:AAPL_NEW")
    )
    message = (
        r"factor identity.*unique canonical IDs"
        if duplicate_canonical
        else r"factor identity.*overlap"
    )
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_dated_spec(), other),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )
    assert _scores(tmp_path / "catalog") == []


@pytest.mark.parametrize("identity_gap", [False, True])
def test_ineligible_row_does_not_bypass_identity_validation(
    tmp_path: Path, identity_gap: bool, factor_repository: TradingRepository
) -> None:
    instrument = _dated_spec()
    if identity_gap:
        instrument = replace(
            instrument, factor_identity_periods=instrument.factor_identity_periods[1:]
        )
    row = {**_rows()[0], "security_id": "eodhd:isin:AAPL_NEW", "score": None, "eligible": False}
    bundle = _write_bundle(tmp_path / "bundle", rows=[row])
    with pytest.raises(ValueError, match="factor identity"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(instrument,),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )
    assert _scores(tmp_path / "catalog") == []


def test_later_identity_failure_does_not_partially_write_history(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    rows = [_rows()[0], {**_rows()[0], "asof_date": date(2025, 1, 3)}]
    bundle = _write_bundle(tmp_path / "bundle", rows=rows, source_kind="evaluation_predictions")
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    with pytest.raises(ValueError, match=r"factor identity.*not valid.*2025-01-03"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(_dated_spec(),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )
    assert _scores(catalog_path) == []


def test_identity_checks_ignore_unrelated_and_inactive_instruments(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    """接收范围外的无 ISIN 股票及尚未活跃的目标不阻止当天交付。"""
    instrument = replace(
        _dated_spec(),
        first_trading_date=date(2025, 1, 3),
        factor_identity_periods=_dated_spec().factor_identity_periods[1:],
    )
    rows = [
        _rows()[0],
        {**_rows()[1], "score": 0.5, "eligible": True},
        {**_rows()[0], "security_id": "eodhd:symbol:UNRELATED.US", "symbol": "UNRELATED"},
    ]

    def metadata(manifest: dict[str, object]) -> None:
        cast(dict[str, object], manifest["input"])["identity_policy"] = (
            "provider_neutral_security_id"
        )

    bundle = _write_bundle(tmp_path / "bundle", rows=rows, mutate_manifest=metadata)
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=(instrument, _spec("MSFT", "eodhd:isin:MSFT")),
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )
    imported = _scores(catalog_path)
    assert [row.canonical_id for row in imported] == ["MSFT.US"]
    assert imported[0].batch_size == 1


def test_identity_can_change_owner_only_in_nonoverlapping_dates(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    instrument = _dated_spec()
    second = _spec("MSFT", "eodhd:isin:AAPL", first_trading_date=date(2025, 1, 3))
    rows = [
        {**_rows()[0], "asof_date": date(2025, 1, 3), "symbol": "MSFT"},
        {**_rows()[0], "asof_date": date(2025, 1, 3), "security_id": "eodhd:isin:AAPL_NEW"},
    ]
    bundle = _write_bundle(tmp_path / "bundle", rows=rows)
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=(instrument, second),
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )
    assert {row.canonical_id: row.security_id for row in _scores(catalog_path)} == {
        "AAPL.US": "eodhd:isin:AAPL_NEW",
        "MSFT.US": "eodhd:isin:AAPL",
    }


def test_nt_catalog_request_routes_factor_scores_to_the_actor_topic(tmp_path: Path) -> None:
    """NT Catalog 历史请求不得因 DataType metadata 不一致而静默丢失整批因子。"""
    catalog = CatalogRepository(tmp_path / "catalog")
    score = FactorScoreData(
        calendar_version=CALENDAR_VERSION,
        canonical_id="AAPL.US",
        security_id="eodhd:isin:US0378331005",
        asof_date="2026-08-12",
        score=1.0,
        eligible=True,
        batch_id="delivery:2026-08-12",
        batch_size=1,
        delivery_id="d" * 64,
        model_release_id="r" * 64,
        source_kind="signal_inference",
        ts_event=10,
        ts_init=10,
    )
    catalog.catalog.write_data([CustomData(FACTOR_DATA_TYPE, score)])

    clock = TestClock()
    clock.set_time(20)
    msgbus = MessageBus(trader_id=TraderId("TESTER-001"), clock=clock)
    engine = DataEngine(msgbus=msgbus, cache=Cache(), clock=clock)
    engine.register_catalog(catalog.catalog)
    received: list[FactorScoreData] = []
    responses: list[object] = []
    topic = TopicCache().get_custom_data_topic(FACTOR_DATA_TYPE, historical=True)
    msgbus.subscribe(topic=topic, handler=received.append)

    msgbus.request(
        endpoint="DataEngine.request",
        request=RequestData(
            data_type=FACTOR_DATA_TYPE,
            instrument_id=None,
            start=datetime(1970, 1, 1, tzinfo=UTC),
            end=datetime(2026, 8, 14, tzinfo=UTC),
            limit=0,
            client_id=ClientId("CATALOG"),
            venue=None,
            callback=responses.append,
            request_id=UUID4(),
            ts_init=clock.timestamp_ns(),
            params={"update_catalog": False, "join_request": False},
        ),
    )

    assert FACTOR_DATA_TYPE.metadata == {}
    assert [value.canonical_id for value in received] == ["AAPL.US"]
    assert len(responses) == 1


def test_real_facdigger_mock_bundle_imports_with_project_identity_mapping(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    """产消者必须直接验证 FacDigger 产出的原始交付, 而不只验证本地构造的 fixture。"""
    instruments = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
    catalog_path = tmp_path / "catalog"
    catalog = CatalogRepository(catalog_path)
    asof_date = date(2026, 8, 12)
    bars = [
        make_bar(
            asof_date,
            instrument_id=instrument.canonical_id,
            bar_type_suffix=bar_type_suffix,
        )
        for instrument in instruments
        for bar_type_suffix in (
            "1-DAY-LAST-INTERNAL",
            "1-DAY-LAST-EXTERNAL",
        )
    ]
    assert catalog.append_new_bars(bars) == 20

    summary = import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=(
            PROJECT_ROOT / "tests" / "fixtures" / "factor_batches" / FACDIGGER_MOCK_DELIVERY_ID
        ),
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )

    assert summary.delivery_id == FACDIGGER_MOCK_DELIVERY_ID
    assert summary.rows_received == 10
    assert summary.rows_imported == 10
    assert summary.dates_imported == 1
    assert summary.instruments_imported == 10
    scores = _scores(catalog_path)
    assert {score.canonical_id for score in scores if score.eligible} == {
        "AAPL.US",
        "AMZN.US",
        "GOOGL.US",
        "JPM.US",
        "META.US",
        "MSFT.US",
        "NVDA.US",
        "XOM.US",
    }
    assert {score.canonical_id for score in scores if not score.eligible} == {
        "JNJ.US",
        "TSLA.US",
    }


@pytest.mark.parametrize(
    "model_type",
    ["financial_pretrained_patchtst", "finance_patch_transformer", "lightgbm"],
)
def test_factor_bundle_imports_nt_custom_data_and_is_idempotent(
    tmp_path: Path, model_type: str, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(
        tmp_path / "bundles",
        mutate_manifest=lambda manifest: cast(dict[str, object], manifest["model"]).update(
            {"model_type": model_type}
        ),
    )
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    instruments = (
        _spec("AAPL", "eodhd:isin:AAPL"),
        _spec("MSFT", "eodhd:isin:MSFT"),
    )

    summary = import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )

    assert summary.rows_received == 2
    assert summary.rows_imported == 2
    assert summary.dates_imported == 1
    assert summary.instruments_imported == 2
    scores = _scores(catalog_path)
    assert len(scores) == 2
    assert {score.batch_size for score in scores} == {2}
    assert all(score.delivery_id == summary.delivery_id for score in scores)
    ineligible = next(score for score in scores if not score.eligible)
    assert ineligible.canonical_id == "MSFT.US"
    assert ineligible.score == 0.0

    repeated = import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
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
        (
            lambda manifest: cast(dict[str, object], manifest["coverage"]).update({"ratio": 1}),
            "finite float",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["coverage"]).update(
                {"missing_eligible_rows": True}
            ),
            "integer",
        ),
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
    factor_repository: TradingRepository,
) -> None:
    bundle = _write_bundle(tmp_path, mutate_manifest=mutation, preserve_delivery_id=True)
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_bundle_rejects_delivery_directory_mismatch(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(tmp_path)
    renamed = bundle.with_name("0" * 64)
    bundle.rename(renamed)
    with pytest.raises(ValueError, match="directory name"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=renamed,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: cast(dict[str, object], manifest["model"]).update(
            {"model_id": "another-valid-model"}
        ),
        lambda manifest: cast(dict[str, object], manifest["model"]).update(
            {"model_type": "finance_patch_transformer"}
        ),
        lambda manifest: cast(dict[str, object], manifest["input"]).update(
            {"snapshot_id": "another-valid-snapshot"}
        ),
        lambda manifest: cast(dict[str, object], manifest["time"]).update(
            {"calendar_version": "2026.2"}
        ),
        lambda manifest: cast(dict[str, object], manifest["coverage"]).update(
            {"expected_eligible_rows": 0, "scored_eligible_rows": 0}
        ),
    ],
)
def test_factor_bundle_semantic_change_requires_new_delivery_id(
    tmp_path: Path,
    mutation: Callable[[dict[str, object]], None],
    factor_repository: TradingRepository,
) -> None:
    bundle = _write_bundle(tmp_path, mutate_manifest=mutation, preserve_delivery_id=True)
    with pytest.raises(ValueError, match="semantic identity"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_bundle_created_at_does_not_change_delivery_identity(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(
        tmp_path / "bundles",
        mutate_manifest=lambda manifest: manifest.update(
            {"created_at": "2025-01-05T00:00:00+00:00"}
        ),
        preserve_delivery_id=True,
    )
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    summary = import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=(
            _spec("AAPL", "eodhd:isin:AAPL"),
            _spec("MSFT", "eodhd:isin:MSFT"),
        ),
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )
    assert summary.rows_imported == 2


def test_factor_bundle_rejects_multiday_signal_inference(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    rows = [
        {
            "security_id": "eodhd:isin:AAPL",
            "symbol": "AAPL",
            "asof_date": session,
            "score": 1.0,
            "eligible": True,
        }
        for session in (date(2025, 1, 2), date(2025, 1, 3))
    ]
    bundle = _write_bundle(tmp_path, rows=rows)
    with pytest.raises(ValueError, match="exactly one"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_bundle_rejects_ineligible_evaluation_rows(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(
        tmp_path,
        rows=[
            {
                "security_id": "eodhd:isin:AAPL",
                "symbol": "AAPL",
                "asof_date": date(2025, 1, 2),
                "score": None,
                "eligible": False,
            }
        ],
        source_kind="evaluation_predictions",
    )
    with pytest.raises(ValueError, match="eligible scored"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
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
                {"model_type": "bad model"}
            ),
            "model_type",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["model"]).update({"model_type": ""}),
            "model_type",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["model"]).update({"model_type": 123}),
            "model_type",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["model"]).update(
                {"score_semantics": "neutralized_score"}
            ),
            "score_semantics",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["input"]).update(
                {"universe_semantics": "wrong"}
            ),
            "universe_semantics",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["input"]).update(
                {"identity_policy": "ticker"}
            ),
            "identity_policy",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["time"]).update({"calendar": "UTC"}),
            "calendar",
        ),
        (
            lambda manifest: cast(dict[str, object], manifest["time"]).update(
                {"calendar_version": ""}
            ),
            "calendar_version",
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
    factor_repository: TradingRepository,
) -> None:
    bundle = _write_bundle(tmp_path, mutate_manifest=mutation, preserve_delivery_id=True)
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_bundle_rejects_unfinalized_or_malformed_delivery(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="finalized"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=missing,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    bundle = _write_bundle(tmp_path / "visible")
    (bundle / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="only"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    hidden = _write_bundle(tmp_path / "hidden")
    (hidden / ".unexpected").write_text("hidden", encoding="utf-8")
    with pytest.raises(ValueError, match="only"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=hidden,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    unreadable = _write_bundle(tmp_path / "manifest")
    (unreadable / "manifest.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=unreadable,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
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
    with pytest.raises(ValueError, match="finite float"):
        _float(1, "field")
    with pytest.raises(ValueError, match="40-character"):
        _git_commit("abc123", "field")


def test_factor_bundle_rejects_artifact_and_universe_tampering(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    artifact = _write_bundle(tmp_path / "artifact")
    with (artifact / "factors.parquet").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="factors hash"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=artifact,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    universe = _write_bundle(
        tmp_path / "universe",
        mutate_manifest=lambda manifest: cast(dict[str, object], manifest["input"]).update(
            {"universe_sha256": "f" * 64}
        ),
    )
    with pytest.raises(ValueError, match="universe hash"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=universe,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_bundle_rejects_noncanonical_parquet_schema(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(tmp_path, score_dtype="float32")
    with pytest.raises(ValueError, match="schema mismatch"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


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
                    "security_id": "eodhd:isin:MSFT",
                    "symbol": "MSFT",
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
            "schema mismatch",
        ),
    ],
)
def test_factor_bundle_rejects_invalid_rows(
    tmp_path: Path,
    rows: list[dict[str, object]],
    message: str,
    factor_repository: TradingRepository,
) -> None:
    bundle = _write_bundle(tmp_path, rows=rows)
    _write_price_bars(tmp_path / "catalog")
    with pytest.raises(ValueError, match=message):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_import_requires_prices_and_complete_production_cross_section(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    missing_row = [row for row in _rows() if row["security_id"] != "eodhd:isin:MSFT"]
    bundle = _write_bundle(tmp_path / "production", rows=missing_row)
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    instruments = (
        _spec("AAPL", "eodhd:isin:AAPL"),
        _spec("MSFT", "eodhd:isin:MSFT"),
    )
    with pytest.raises(ValueError, match="complete factor cross-section"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=instruments,
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    evaluation = _write_bundle(
        tmp_path / "evaluation",
        rows=missing_row,
        source_kind="evaluation_predictions",
    )
    _write_price_bars(tmp_path / "evaluation-catalog")
    summary = import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=evaluation,
        catalog_path=tmp_path / "evaluation-catalog",
        instruments=(_spec("MSFT", "eodhd:isin:MSFT"),),
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )
    assert summary.rows_imported == 1
    assert all(not score.eligible for score in _scores(tmp_path / "evaluation-catalog"))


def test_factor_import_rejects_missing_prices_for_eligible_row(
    tmp_path: Path, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(tmp_path / "bundles")
    with pytest.raises(ValueError, match="no signal bar"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=tmp_path / "catalog",
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    catalog_path = tmp_path / "signal-only-catalog"
    catalog = CatalogRepository(catalog_path)
    catalog.append_new_bars(
        [
            make_bar(
                date(2025, 1, 2),
                instrument_id="AAPL.US",
                bar_type_suffix="1-DAY-LAST-INTERNAL",
            )
        ]
    )
    with pytest.raises(ValueError, match="no execution bar"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(_spec("AAPL", "eodhd:isin:AAPL"),),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


def test_factor_import_rejects_missing_mapping_lifecycle_and_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factor_repository: TradingRepository
) -> None:
    bundle = _write_bundle(tmp_path / "bundles")
    catalog_path = tmp_path / "catalog"
    _write_price_bars(catalog_path)
    without_mapping = _spec("AAPL", "eodhd:isin:AAPL")
    without_mapping = replace(without_mapping, factor_security_id=None)
    with pytest.raises(ValueError, match="No instruments"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(without_mapping,),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    inactive = _spec(
        "AAPL",
        "eodhd:isin:AAPL",
        first_trading_date=date(2026, 1, 1),
    )
    with pytest.raises(ValueError, match="no rows mapped"):
        import_factor_bundle(
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=(inactive,),
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )

    instruments = (
        _spec("AAPL", "eodhd:isin:AAPL"),
        _spec("MSFT", "eodhd:isin:MSFT"),
    )
    import_factor_bundle(
        mode="historical",
        repository=factor_repository,
        bundle_dir=bundle,
        catalog_path=catalog_path,
        instruments=instruments,
        signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
    )
    changed = _scores(catalog_path)[0]
    replacement = FactorScoreData(
        calendar_version=CALENDAR_VERSION,
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
            mode="historical",
            repository=factor_repository,
            bundle_dir=bundle,
            catalog_path=catalog_path,
            instruments=instruments,
            signal_bar_type_suffix="1-DAY-LAST-INTERNAL",
            execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        )


@pytest.fixture
def factor_repository(tmp_path: Path) -> Iterator[TradingRepository]:
    """隔离每次测试的实际验收记录。"""
    repository = TradingRepository(f"sqlite:///{tmp_path}/factor-imports.db")
    repository.create_schema()
    try:
        yield repository
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("at", "accepted"),
    [
        (datetime(2025, 1, 3, 14, 29, 59, 999999, tzinfo=UTC), True),
        (datetime(2025, 1, 3, 14, 30, tzinfo=UTC), False),
        (datetime(2025, 1, 3, 15, tzinfo=UTC), False),
        (datetime(2025, 1, 6, 10, tzinfo=UTC), False),
    ],
)
def test_paper_import_receipt_uses_completion_clock_and_strict_cutoff(
    tmp_path: Path,
    factor_repository: TradingRepository,
    at: datetime,
    accepted: bool,
) -> None:
    bundle = _write_bundle(
        tmp_path / "bundles",
        mutate_manifest=lambda m: m.update({"created_at": "2025-01-03T00:00:00+00:00"}),
    )
    catalog = tmp_path / "catalog"
    _write_price_bars(catalog)
    kwargs = {
        "bundle_dir": bundle,
        "catalog_path": catalog,
        "instruments": (_spec("AAPL", "eodhd:isin:AAPL"), _spec("MSFT", "eodhd:isin:MSFT")),
        "signal_bar_type_suffix": "1-DAY-LAST-INTERNAL",
        "execution_bar_type_suffix": "1-DAY-LAST-EXTERNAL",
        "mode": "paper",
        "expected_release_id": "2" * 64,
        "repository": factor_repository,
        "clock": lambda: at,
    }
    if accepted:
        summary = import_factor_bundle(**kwargs)
        receipt = factor_repository.get_factor_import(
            catalog_path=str(catalog.resolve()), delivery_id=summary.delivery_id, mode="paper"
        )
        assert receipt is not None
        assert receipt.verified_at == at
        kwargs["clock"] = lambda: datetime(2025, 1, 4, tzinfo=UTC)
        assert import_factor_bundle(**kwargs).already_imported
        assert (
            factor_repository.get_factor_import(
                catalog_path=str(catalog.resolve()), delivery_id=summary.delivery_id, mode="paper"
            )
            == receipt
        )
    else:
        with pytest.raises(ValueError, match=r"cutoff|expected date"):
            import_factor_bundle(**kwargs)
        assert (
            factor_repository.get_factor_import(
                catalog_path=str(catalog.resolve()), delivery_id=bundle.name, mode="paper"
            )
            is None
        )
        # Catalog 写入存在不等于接纳成功, 重试不能倒填此前的时间。
        with pytest.raises(ValueError, match=r"cutoff|expected date"):
            import_factor_bundle(**kwargs)


def test_historical_receipt_cannot_authorize_late_paper_import(
    tmp_path: Path,
    factor_repository: TradingRepository,
) -> None:
    bundle = _write_bundle(tmp_path / "bundles")
    catalog = tmp_path / "catalog"
    _write_price_bars(catalog)
    kwargs = {
        "bundle_dir": bundle,
        "catalog_path": catalog,
        "instruments": (_spec("AAPL", "eodhd:isin:AAPL"), _spec("MSFT", "eodhd:isin:MSFT")),
        "signal_bar_type_suffix": "1-DAY-LAST-INTERNAL",
        "execution_bar_type_suffix": "1-DAY-LAST-EXTERNAL",
        "repository": factor_repository,
        "clock": lambda: datetime(2026, 9, 16, tzinfo=UTC),
    }
    result = import_factor_bundle(mode="historical", **kwargs)
    assert result.rows_imported == 2
    with pytest.raises(ValueError, match=r"expected date|cutoff"):
        import_factor_bundle(mode="paper", expected_release_id="2" * 64, **kwargs)
    assert (
        factor_repository.get_factor_import(
            catalog_path=str(catalog.resolve()), delivery_id=result.delivery_id, mode="paper"
        )
        is None
    )


@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("2026-11-27T17:59:59+00:00", date(2026, 11, 25)),
        ("2026-11-27T18:00:00+00:00", date(2026, 11, 27)),
        ("2026-03-09T19:59:59+00:00", date(2026, 3, 6)),
        ("2026-03-09T20:00:00+00:00", date(2026, 3, 9)),
        ("2026-03-08T12:00:00+00:00", date(2026, 3, 6)),
    ],
)
def test_expected_factor_day_tracks_actual_close(clock: str, expected: date) -> None:
    from trading_assistant.data.factor import expected_factor_date

    assert expected_factor_date(datetime.fromisoformat(clock)) == expected


@pytest.mark.parametrize(
    ("day", "hours", "opening", "expiry"),
    [
        (date(2026, 11, 25), 24, "2026-11-27T14:30:00+00:00", "2026-11-27T18:00:00+00:00"),
        (date(2026, 11, 25), 1, "2026-11-27T14:30:00+00:00", "2026-11-27T15:30:00+00:00"),
        (date(2026, 3, 6), 24, "2026-03-09T13:30:00+00:00", "2026-03-09T20:00:00+00:00"),
    ],
)
def test_factor_execution_window_ends_at_actual_close_or_ttl(
    day: date,
    hours: int,
    opening: str,
    expiry: str,
) -> None:
    from trading_assistant.data.factor import factor_execution_window

    assert factor_execution_window(day, hours) == (
        datetime.fromisoformat(opening),
        datetime.fromisoformat(expiry),
    )
