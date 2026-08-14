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
            f"watchdog gap too large on {utc_day.date()}: "
            f"max={max(gaps)} local={list(map(_fmt_hm, local))}"
        )


def validate() -> None:
    primary_text = PRIMARY.read_text(encoding="utf-8")
    watchdog_text = WATCHDOG.read_text(encoding="utf-8")

    # 新架构必须复用上一收盘 V6 Artifact，并由盘中确定性确认器做最终执行判断。
    for marker in (
        "scripts/run_us_open_confirmation.py",
        "v6-daily-*",
        "v6_daily_latest.json",
        "OPEN_CONFIRMATION_STARTER_POSITION_PCT",
        "us-open-confirmation-${{ steps.gate.outputs.ny_date }}",
    ):
        if marker not in primary_text:
            raise AssertionError(f"primary confirmation missing actionable marker: {marker}")

    # 盘中确认不得再触发完整 V4 -> V6 重跑，否则会退化回重复日报。
    for forbidden in (
        "00-daily-analysis.yml",
        "upstream_run_id",
        "waitForCompletion",
    ):
        if forbidden in primary_text:
            raise AssertionError(
                f"primary confirmation still contains legacy full-analysis orchestration: {forbidden}"
            )

    # Watchdog 必须用开盘确认 Artifact，而不是新的 V6 workflow_dispatch，作为真实发送覆盖信号。
    for marker in (
        "workflow_id: '01-us-open-confirmation.yml'",
        "listWorkflowRunArtifacts",
        "us-open-confirmation-",
        "No successful open-confirmation Artifact",
    ):
        if marker not in watchdog_text:
            raise AssertionError(f"watchdog missing open-confirmation coverage marker: {marker}")
    if "workflow_id: '03-v6-daily.yml'" in watchdog_text:
        raise AssertionError("watchdog must not use V6 rerun as open-confirmation coverage")

    # Exercise one EDT and one EST weekday.
    summer = datetime(2026, 8, 13, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 11, tzinfo=timezone.utc)
    for day in (summer, winter):
        _assert_primary_mapping(primary_text, day)
        _assert_watchdog_coverage(watchdog_text, day)

    print("US open reliability backtest: PASS")


if __name__ == "__main__":
    validate()
