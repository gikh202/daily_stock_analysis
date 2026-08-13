#!/usr/bin/env python3
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ROOT / ".github/workflows/01-us-open-confirmation.yml"
WATCHDOG = ROOT / ".github/workflows/01-us-open-schedule-watchdog.yml"
NY = ZoneInfo("America/New_York")

PRIMARY_LOCAL = ("09:45", "09:55", "10:10", "10:25", "10:40", "10:55", "11:10")
WINDOW_START = 9 * 60 + 45
WINDOW_END = 12 * 60 + 30
MAX_WATCHDOG_GAP_MINUTES = 20


def _cron_specs(text: str) -> list[str]:
    return re.findall(r"- cron:\s*['\"]([^'\"]+)['\"]", text)


def _expand_field(field: str) -> list[int]:
    values: list[int] = []
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            values.extend(range(start, end + 1))
        else:
            values.append(int(part))
    return sorted(set(values))


def _utc_candidates(specs: list[str], utc_day: datetime) -> list[datetime]:
    out: list[datetime] = []
    for spec in specs:
        fields = spec.split()
        if len(fields) != 5:
            raise AssertionError(f"unsupported cron: {spec}")
        minutes = _expand_field(fields[0])
        hours = _expand_field(fields[1])
        for hour in hours:
            for minute in minutes:
                out.append(
                    datetime(
                        utc_day.year,
                        utc_day.month,
                        utc_day.day,
                        hour,
                        minute,
                        tzinfo=timezone.utc,
                    )
                )
    return sorted(out)


def _local_hm(dt: datetime) -> int:
    local = dt.astimezone(NY)
    return local.hour * 60 + local.minute


def _fmt_hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _assert_primary_mapping(text: str, utc_day: datetime) -> None:
    specs = _cron_specs(text)
    local = sorted(
        {
            _fmt_hm(_local_hm(dt))
            for dt in _utc_candidates(specs, utc_day)
            if WINDOW_START <= _local_hm(dt) <= 11 * 60 + 15
        }
    )
    missing = sorted(set(PRIMARY_LOCAL) - set(local))
    if missing:
        raise AssertionError(
            f"primary confirmation misses {missing} on {utc_day.date()} local={local}"
        )


def _assert_watchdog_coverage(text: str, utc_day: datetime) -> None:
    specs = _cron_specs(text)
    local = sorted(
        {
            _local_hm(dt)
            for dt in _utc_candidates(specs, utc_day)
            if WINDOW_START <= _local_hm(dt) <= WINDOW_END
        }
    )
    if not local:
        raise AssertionError(f"watchdog has no candidates on {utc_day.date()}")
    checkpoints = [WINDOW_START, *local, WINDOW_END]
    gaps = [b - a for a, b in zip(checkpoints, checkpoints[1:])]
    if max(gaps) > MAX_WATCHDOG_GAP_MINUTES:
        raise AssertionError(
            f"watchdog gap too large on {utc_day.date()}: max={max(gaps)} local={list(map(_fmt_hm, local))}"
        )


def validate() -> None:
    primary_text = PRIMARY.read_text(encoding="utf-8")
    watchdog_text = WATCHDOG.read_text(encoding="utf-8")

    # Explicit V4 -> V6 orchestration and exact upstream binding are required.
    for marker in (
        "00-daily-analysis.yml",
        "03-v6-daily.yml",
        "upstream_run_id",
        "waitForCompletion",
    ):
        if marker not in primary_text:
            raise AssertionError(f"primary confirmation missing orchestration marker: {marker}")

    # Coverage must be judged by the downstream final V6, not by a primary
    # workflow that can finish success after a gate skip.
    if "workflow_id: '03-v6-daily.yml'" not in watchdog_text:
        raise AssertionError("watchdog must use V6 final-report coverage as its success signal")
    if "No successful/in-flight automated V6 final report found" not in watchdog_text:
        raise AssertionError("watchdog fallback reason is missing")

    # Exercise one EDT and one EST date. These are deliberately ordinary
    # weekdays, so the test validates DST mapping without holiday semantics.
    summer = datetime(2026, 8, 13, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 11, tzinfo=timezone.utc)
    for day in (summer, winter):
        _assert_primary_mapping(primary_text, day)
        _assert_watchdog_coverage(watchdog_text, day)

    print("US open reliability backtest: PASS")


if __name__ == "__main__":
    validate()
