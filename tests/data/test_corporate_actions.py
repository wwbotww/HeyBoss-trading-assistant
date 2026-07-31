"""公司行动 sidecar 仓储测试。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from trading_assistant.data.corporate_actions import (
    CorporateActionRepository,
    CorporateActions,
    DividendAction,
    SplitAction,
    corporate_action_path,
)


def test_repository_round_trip_is_atomic_and_idempotent(tmp_path: Path) -> None:
    """合法公司行动应可无损读取, 相同内容不得重复改写。"""
    repository = CorporateActionRepository(tmp_path / "actions")
    actions = CorporateActions(
        instrument_id="AAPL.US",
        dividends=(DividendAction(date(2026, 5, 11), Decimal("0.26"), Decimal("0.26"), "USD"),),
        splits=(SplitAction(date(2020, 8, 31), Decimal("4")),),
    )

    assert repository.write(actions)
    assert repository.read("AAPL.US") == actions
    assert not repository.write(actions)
    assert not (tmp_path / "actions" / "AAPL.tmp").exists()


def test_repository_handles_missing_and_rejects_invalid_content(tmp_path: Path) -> None:
    """缺失文件返回空记录; 损坏内容必须显式失败。"""
    repository = CorporateActionRepository(tmp_path / "actions")
    assert repository.read("MSFT.US") == CorporateActions("MSFT.US", (), ())

    path = tmp_path / "actions" / "MSFT.US.json"
    path.write_text(
        '{"instrument_id":"MSFT.US","dividends":[null],"splits":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="条目无效"):
        repository.read("MSFT.US")
    with pytest.raises(ValueError, match="不安全字符"):
        repository.read("../MSFT.US")


def test_corporate_action_path_is_stable_sibling(tmp_path: Path) -> None:
    """sidecar 路径不得携带 manifest 或运行版本号。"""
    assert corporate_action_path(tmp_path / "catalog") == tmp_path / "catalog-actions"
