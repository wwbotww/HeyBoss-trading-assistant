"""账户与持仓查询测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.data.helpers import make_bar
from trading_assistant.application.models import QuerySourceError
from trading_assistant.application.portfolio import PortfolioQueryService, mask_account_id
from trading_assistant.data.catalog import CatalogRepository
from trading_assistant.data.config import InstrumentSpec
from trading_assistant.storage.repository import PositionSnapshotInput, TradingRepository


def _spec() -> InstrumentSpec:
    return InstrumentSpec(
        symbol="AAPL",
        instrument_id="AAPL.US",
        live_instrument_id="AAPL.NASDAQ",
        data_symbol="AAPL.US",
        exchange="SMART",
        primary_exchange="NASDAQ",
        currency="USD",
        price_precision=4,
        price_increment="0.0100",
        lot_size=1,
    )


def _service(
    repository: TradingRepository | None,
    tmp_path: Path,
    *,
    account_id: str | None = "IB-DU12345678",
) -> PortfolioQueryService:
    return PortfolioQueryService(
        repository=repository,
        account_id=account_id,
        catalog_path=tmp_path / "catalog",
        instruments=(_spec(),),
        execution_bar_type_suffix="1-DAY-LAST-EXTERNAL",
        stale_after_seconds=60,
        clock=lambda: datetime(2026, 8, 15, 0, 2, tzinfo=UTC),
    )


def test_portfolio_masks_account_maps_instrument_and_adds_eod_reference(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path}/live.db"
    writer = TradingRepository(database_url)
    writer.create_schema()
    writer.record_portfolio_snapshot(
        timestamp_ns=int(datetime(2026, 8, 15, tzinfo=UTC).timestamp() * 1_000_000_000),
        account_id="IB-DU12345678",
        currency="USD",
        net_liquidation=1_000.0,
        free_cash=800.0,
        locked_cash=200.0,
        positions=(
            PositionSnapshotInput(
                instrument_id="AAPL.NASDAQ",
                signed_quantity=2,
                side="LONG",
                avg_open_price=90,
                realized_pnl=5,
            ),
        ),
    )
    writer.record_portfolio_snapshot(
        timestamp_ns=int(datetime(2026, 8, 14, 23, 59, tzinfo=UTC).timestamp() * 1_000_000_000),
        account_id="IB-DU12345678",
        currency="USD",
        net_liquidation=900.0,
        free_cash=900.0,
        locked_cash=0.0,
        positions=(),
    )
    writer.close()
    catalog = CatalogRepository(tmp_path / "catalog")
    catalog.append_new_bars(
        [
            make_bar(
                date(2026, 8, 14),
                instrument_id="AAPL.US",
                bar_type_suffix="1-DAY-LAST-EXTERNAL",
                close=100,
            )
        ]
    )

    reader = TradingRepository(database_url, read_only=True)
    service = _service(reader, tmp_path)
    portfolio = service.latest()

    assert portfolio.source_state == "available"
    assert portfolio.account_id == "IB-••••5678"
    assert portfolio.is_stale is True
    assert portfolio.positions[0].canonical_id == "AAPL.US"
    assert portfolio.positions[0].source_instrument_id == "AAPL.NASDAQ"
    assert portfolio.positions[0].reference_price == 100
    assert portfolio.positions[0].estimated_market_value == 200
    assert portfolio.positions[0].estimated_weight == 0.2
    assert portfolio.positions[0].price_kind == "EOD_EXTERNAL"

    first = service.history(offset=0, limit=1)
    second = service.history(offset=1, limit=1)
    assert first.has_more is True
    assert first.items[0].account_id == "IB-••••5678"
    assert second.has_more is False
    assert second.items[0].net_liquidation == 900
    reader.close()


def test_portfolio_empty_missing_unconfigured_and_invalid_sources(tmp_path: Path) -> None:
    assert _service(None, tmp_path).latest().source_state == "missing"
    assert _service(None, tmp_path, account_id=None).latest().source_state == "unconfigured"
    assert _service(None, tmp_path).history(offset=0, limit=10).items == ()
    assert mask_account_id("DU1") == "••••1"

    database_url = f"sqlite:///{tmp_path}/empty.db"
    writer = TradingRepository(database_url)
    writer.create_schema()
    writer.close()
    reader = TradingRepository(database_url, read_only=True)
    assert _service(reader, tmp_path).latest().source_state == "empty"
    reader.close()

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    broken = TradingRepository(f"sqlite:///{corrupt}", read_only=True)
    with pytest.raises(QuerySourceError, match="账户审计库"):
        _service(broken, tmp_path).latest()
    broken.close()
