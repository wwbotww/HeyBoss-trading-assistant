"""NautilusTrader ParquetDataCatalog 的最小仓储封装。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog


def _catalog_safe_instrument(instrument: Instrument) -> Instrument:
    """复制 NT 原生 Instrument、并移除 IB API 的不可序列化附加元数据。"""
    data = instrument.to_dict(instrument)
    data["info"] = None
    sanitized = instrument.__class__.from_dict(data)
    if not isinstance(sanitized, Instrument):
        raise TypeError("Instrument deserialization returned an unexpected type")
    return sanitized


class CatalogRepository:
    """只存取 NT 原生 Instrument 与 Bar。"""

    def __init__(self, path: Path) -> None:
        absolute_path = path.expanduser().resolve()
        absolute_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(absolute_path)

    @property
    def catalog(self) -> ParquetDataCatalog:
        """返回底层 NT Catalog; 供后续 BacktestNode 配置复用。"""
        return self._catalog

    def write_instruments(self, instruments: Sequence[Instrument]) -> int:
        """只写入 Catalog 中尚不存在的标的定义。"""
        written = 0
        for instrument in instruments:
            existing = self._catalog.instruments(instrument_ids=[instrument.id.value])
            if existing:
                continue
            self._catalog.write_data([_catalog_safe_instrument(instrument)])
            written += 1
        return written

    def latest_bar_timestamp(self, bar_type: BarType) -> int | None:
        """返回指定 BarType 的最后初始化时间戳。"""
        timestamp = self._catalog.query_last_timestamp(Bar, identifier=str(bar_type))
        return None if timestamp is None else int(timestamp.value)

    def earliest_bar_timestamp(self, bar_type: BarType) -> int | None:
        """返回指定 BarType 的最早初始化时间戳。"""
        timestamp = self._catalog.query_first_timestamp(Bar, identifier=str(bar_type))
        return None if timestamp is None else int(timestamp.value)

    def read_bars(
        self,
        bar_type: BarType,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> list[Bar]:
        """按 BarType 和可选纳秒区间读取 Bar。"""
        bars = self._catalog.bars(
            bar_types=[str(bar_type)],
            start=start_ns,
            end=end_ns,
        )
        return sorted(bars, key=lambda bar: bar.ts_init)

    def append_new_bars(self, bars: Sequence[Bar]) -> int:
        """按 BarType 过滤重复和既有时间戳后写入缺失 Bar。"""
        grouped: dict[str, list[Bar]] = defaultdict(list)
        for bar in bars:
            grouped[str(bar.bar_type)].append(bar)

        written = 0
        for values in grouped.values():
            bar_type = values[0].bar_type
            unique = {bar.ts_init: bar for bar in values}
            earliest = self.earliest_bar_timestamp(bar_type)
            latest = self.latest_bar_timestamp(bar_type)
            if earliest is None or latest is None:
                batches = [[unique[timestamp] for timestamp in sorted(unique)]]
            else:
                batches = [
                    [unique[timestamp] for timestamp in sorted(unique) if timestamp < earliest],
                    [unique[timestamp] for timestamp in sorted(unique) if timestamp > latest],
                ]
            for batch in batches:
                if not batch:
                    continue
                self._catalog.write_data(batch)
                written += len(batch)
        return written

    def replace_bars(self, bars: Sequence[Bar]) -> int:
        """成组替换完整 BarType; 写入失败时尽力恢复全部旧序列。"""
        grouped: dict[str, list[Bar]] = defaultdict(list)
        for bar in bars:
            grouped[str(bar.bar_type)].append(bar)

        changes: list[tuple[str, list[Bar], list[Bar]]] = []
        for identifier, values in grouped.items():
            unique = {bar.ts_init: bar for bar in values}
            incoming = [unique[timestamp] for timestamp in sorted(unique)]
            existing = self.read_bars(incoming[0].bar_type)
            if existing == incoming:
                continue
            changes.append((identifier, incoming, existing))

        try:
            for identifier, _, _ in changes:
                self._catalog.delete_data_range(Bar, identifier=identifier)
            for _, incoming, _ in changes:
                self._catalog.write_data(incoming)
        except Exception:
            for identifier, _, existing in changes:
                self._catalog.delete_data_range(Bar, identifier=identifier)
                if existing:
                    self._catalog.write_data(existing)
            raise
        return sum(len(incoming) for _, incoming, _ in changes)

    def replace_bar_range(
        self,
        bars: Sequence[Bar],
        *,
        start_ns: int,
        end_ns: int,
    ) -> int:
        """只替换闭区间内的 Bar; 任一 BarType 写入失败时恢复全部旧窗口。"""
        if start_ns > end_ns:
            raise ValueError("bar replacement start_ns must not exceed end_ns")
        grouped: dict[str, list[Bar]] = defaultdict(list)
        for bar in bars:
            if not start_ns <= bar.ts_init <= end_ns:
                raise ValueError("replacement bars must stay inside the requested range")
            grouped[str(bar.bar_type)].append(bar)

        changes: list[tuple[str, list[Bar], list[Bar]]] = []
        for identifier, values in grouped.items():
            unique = {bar.ts_init: bar for bar in values}
            incoming = [unique[timestamp] for timestamp in sorted(unique)]
            existing = self.read_bars(
                incoming[0].bar_type,
                start_ns=start_ns,
                end_ns=end_ns,
            )
            if existing == incoming:
                continue
            changes.append((identifier, incoming, existing))

        try:
            for identifier, _, _ in changes:
                self._catalog.delete_data_range(
                    Bar,
                    identifier=identifier,
                    start=start_ns,
                    end=end_ns,
                )
            for _, incoming, _ in changes:
                self._catalog.write_data(incoming)
        except Exception:
            for identifier, _, existing in changes:
                self._catalog.delete_data_range(
                    Bar,
                    identifier=identifier,
                    start=start_ns,
                    end=end_ns,
                )
                if existing:
                    self._catalog.write_data(existing)
            raise
        return sum(len(incoming) for _, incoming, _ in changes)
