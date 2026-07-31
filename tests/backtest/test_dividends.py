"""股息 SimulationModule 生命周期测试。"""

from pathlib import Path
from typing import cast

from nautilus_trader.common.component import Logger
from nautilus_trader.core.data import Data

from trading_assistant.backtest.dividends import (
    DividendSimulationConfig,
    DividendSimulationModule,
)


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


def test_module_writes_empty_diagnostics_and_resets(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "report" / "nt-equity.json"
    module = DividendSimulationModule(
        DividendSimulationConfig(
            corporate_action_directory=str(tmp_path / "actions"),
            instrument_ids=("SPY.US",),
            execution_bar_types={"SPY.US": "SPY.US-1-DAY-LAST-EXTERNAL"},
            account_id="US-001",
            snapshot_path=str(snapshot_path),
        )
    )
    module.pre_process(cast(Data, object()))
    module._latest_prices["SPY.US"] = 100
    module._snapshot_due = True
    module.reset()
    assert module._latest_prices == {}
    assert not module._snapshot_due

    logger = _Logger()
    module.log_diagnostics(cast(Logger, logger))
    assert snapshot_path.read_text(encoding="utf-8") == "[]\n"
    assert logger.messages == ["Dividend simulation snapshots=0, actions=0"]
