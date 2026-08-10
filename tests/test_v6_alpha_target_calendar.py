from __future__ import annotations

from src.v6_daily.lab_replay import _spy_future_return, _spy_market_regime


def _spy_rows() -> list[dict]:
    rows = []
    for day in range(1, 91):
        # Deliberately skip every 7th synthetic date to emulate a benchmark holiday
        # while another market can still have a local session.
        if day % 7 == 0:
            continue
        date_value = f"2025-{((day - 1) // 28) + 1:02d}-{((day - 1) % 28) + 1:02d}"
        close = 100.0 + day
        rows.append({"date": date_value, "close": close})
    return rows


def test_spy_future_return_uses_stock_calendar_window_endpoints() -> None:
    rows = _spy_rows()
    series = {"SPY": rows}

    # 2025-01-07 and 2025-01-14 are absent from SPY in this synthetic calendar.
    # The benchmark must use the last SPY bar at-or-before each actual stock date,
    # rather than advance a fixed number of SPY sessions.
    actual = _spy_future_return(
        series,
        start_date="2025-01-07",
        end_date="2025-01-14",
    )
    expected = (113.0 / 106.0 - 1.0) * 100.0

    assert actual is not None
    assert round(actual, 8) == round(expected, 8)


def test_spy_regime_uses_latest_bar_at_or_before_cross_market_date() -> None:
    rows = _spy_rows()
    series = {"SPY": rows}

    # 2025-03-07 is a skipped SPY date, but more than 60 prior SPY observations
    # exist. The as-of contract should classify from the latest prior SPY bar,
    # not fall into unknown merely because the exact calendar date is absent.
    trend, volatility = _spy_market_regime(series, as_of="2025-03-07")

    assert trend == "up"
    assert volatility in {"expanding", "contracting"}
