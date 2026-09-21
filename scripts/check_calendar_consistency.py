"""通过两个项目各自的解释器比较日历结果, 不产生运行时跨仓依赖。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

PROBE = """
import importlib, json, sys
from datetime import date, timedelta
from importlib.metadata import version
request = json.load(sys.stdin)
module = importlib.import_module(request["module"])
day, end = date.fromisoformat(request["start"]), date.fromisoformat(request["end"])
sessions = [item.isoformat() for item in module.regular_sessions(day, end)]
rows = []
while day <= end:
    session = module.regular_session(day)
    rows.append([day.isoformat(), session.open_utc.isoformat() if session else None,
                 session.close_utc.isoformat() if session else None,
                 module.previous_regular_session(day).isoformat(),
                 module.next_regular_session(day).isoformat()])
    day += timedelta(days=1)
print(json.dumps({"calendar": module.CALENDAR_VERSION, "rows": rows, "sessions": sessions,
                  "environment": {name: version(name) for name in
                      ["exchange_calendars", "pandas", "numpy", "tzdata"]}}))
"""


def main() -> int:
    """差异时返回非零并输出首个不一致的日期。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facdigger-root", type=Path, required=True)
    parser.add_argument("--facdigger-python", type=Path, required=True)
    parser.add_argument("--heyboss-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2000, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2027, 12, 31))
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("start must not be after end")
    root = Path(__file__).resolve().parents[1]
    relative_fixture = Path("tests/fixtures/us_equities_sessions.json")
    if (root / relative_fixture).read_bytes() != (
        args.facdigger_root / relative_fixture
    ).read_bytes():
        raise ValueError("shared calendar fixtures differ")
    results: list[dict[str, Any]] = []
    for python, cwd, module in (
        (args.facdigger_python, args.facdigger_root, "facdigger.data.market_calendar"),
        (args.heyboss_python, root, "trading_assistant.data.market_calendar"),
    ):
        result = subprocess.run(  # noqa: S603 — 明确指定解释器, 无 shell 插值。
            [str(python.expanduser().absolute()), "-c", PROBE],
            cwd=cwd,
            input=json.dumps({"module": module, "start": str(args.start), "end": str(args.end)}),
            capture_output=True,
            text=True,
            check=True,
        )
        results.append(json.loads(result.stdout))
    left, right = results
    if left["calendar"] != right["calendar"] or left["sessions"] != right["sessions"]:
        raise ValueError("calendar source or full session-date sets differ")
    if left["rows"] != right["rows"]:
        mismatch = next(
            ((a, b) for a, b in zip(left["rows"], right["rows"], strict=True) if a != b),
            (left["calendar"], right["calendar"]),
        )
        raise ValueError(f"calendar mismatch: {mismatch}")
    print(
        json.dumps(
            {
                "matched_dates": len(left["rows"]),
                "matched_sessions": len(left["sessions"]),
                "calendar": left["calendar"],
                "facdigger": left["environment"],
                "heyboss": right["environment"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
