from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from src.alpha_engine.models import AlphaFeatures
from src.v6_daily.accuracy import (
    build_horizon_forecasts,
    deterministic_catalyst_score,
)
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.free_sources import _fred_derived, _fundamental_snapshot
from src.v6_daily.replay import replay_series
from src.v6_daily.store import V6DailyStore, mature_outcomes


def _record(history_id: int = 1, code: str = "MSFT", date: str = "2026-01-01") -> dict:
    snapshot = {
        "trend_analysis": {
            "signal_score": 80,
            "current_price": 100.0,
            "support_levels": [95.0],
            "resistance_levels": [115.0],
        },
        "volume_price_features": {"rvol20": 1.4},
        "prediction_context": {
            "horizons": {
                "5d": {"target_return_pct": 2.0},
                "20d": {"target_return_pct": 7.0, "excess_vs_spy_pct": 3.0, "excess_vs_qqq_pct": 2.0, "excess_vs_sector_pct": 1.5},
                "60d": {"target_return_pct": 12.0, "excess_vs_spy_pct": 6.0, "excess_vs_qqq_pct": 4.0, "excess_vs_sector_pct": 3.0},
            },
            "realized_vol_20d_pct": 18.0,
        },
        "fundamental_context": {"quality_score": 88},
        "earnings_event": {"days_until_earnings": 30},
        "market_regime": {"regime": "risk_on", "market_breadth": {"breadth": "broad"}},
        "market_structure_context": {"support": 95.0, "resistance": 115.0},
        "realtime": {"price": 100.0},
        "effective_daily_bar_date": date,
        "atr": 2.0,
    }
    raw = {
        "success": True,
        "model_used": "deepseek/deepseek-v4-flash",
        "dashboard": {"intelligence": {"positive_catalysts": ["LLM prose must not be scored"]}},
    }
    return {
        "id": history_id,
        "query_id": f"q-{history_id}",
        "code": code,
        "created_at": f"{date} 22:30:00",
        "context_snapshot": json.dumps(snapshot),
        "raw_result": json.dumps(raw),
    }


def test_horizon_models_are_distinct_and_etf_ignores_company_fundamental() -> None:
    low = AlphaFeatures(
        trend=70,
        momentum=80,
        relative_strength=75,
        sector_relative_strength=70,
        volume_confirmation=85,
        fundamental_quality=20,
        market_regime=75,
        catalyst=60,
    )
    high = replace(low, fundamental_quality=95)
    stock_low = build_horizon_forecasts(low, instrument_type="STOCK")
    stock_high = build_horizon_forecasts(high, instrument_type="STOCK")
    assert stock_low["5d"]["score"] == stock_high["5d"]["score"]
    assert stock_low["20d"]["score"] < stock_high["20d"]["score"]
    assert stock_high["5d"]["weights"] != stock_high["20d"]["weights"]

    etf_low = build_horizon_forecasts(low, instrument_type="ETF")
    etf_high = build_horizon_forecasts(high, instrument_type="ETF")
    assert etf_low["20d"]["score"] == etf_high["20d"]["score"]


def test_free_form_llm_catalyst_is_not_scored_but_structured_evidence_is() -> None:
    score, diag = deterministic_catalyst_score(
        {"dashboard": {"intelligence": {"positive_catalysts": ["huge upside"]}}}
    )
    assert score is None
    assert diag["eligible_events"] == 0

    score, diag = deterministic_catalyst_score(
        {
            "dashboard": {
                "intelligence": {
                    "evidence": [
                        {
                            "direction": "positive",
                            "source_type": "SEC 8-K official",
                            "importance": "high",
                            "age_hours": 2,
                        }
                    ]
                }
            }
        }
    )
    assert score is not None and score > 50
    assert diag["eligible_events"] == 1


def _fact(tag: str, values: list[tuple[str, float]]) -> tuple[str, dict]:
    rows = [
        {"end": end, "val": value, "form": "10-K", "fp": "FY", "filed": f"{int(end[:4]) + 1}-02-01"}
        for end, value in values
    ]
    return tag, {"units": {"USD": rows}}


def test_sec_companyfacts_builds_deterministic_fundamental_quality() -> None:
    revenue_tag, revenue = _fact(
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        [("2025-12-31", 120.0), ("2024-12-31", 100.0)],
    )
    op_tag, op = _fact("OperatingIncomeLoss", [("2025-12-31", 36.0), ("2024-12-31", 28.0)])
    net_tag, net = _fact("NetIncomeLoss", [("2025-12-31", 30.0), ("2024-12-31", 24.0)])
    ocf_tag, ocf = _fact("NetCashProvidedByUsedInOperatingActivities", [("2025-12-31", 40.0), ("2024-12-31", 34.0)])
    capex_tag, capex = _fact("PaymentsToAcquirePropertyPlantAndEquipment", [("2025-12-31", 10.0), ("2024-12-31", 9.0)])
    payload = {
        "facts": {
            "us-gaap": {
                revenue_tag: revenue,
                op_tag: op,
                net_tag: net,
                ocf_tag: ocf,
                capex_tag: capex,
            }
        }
    }
    result = _fundamental_snapshot(payload)
    assert result["revenue_yoy_pct"] == 20.0
    assert result["operating_margin_pct"] == 30.0
    assert result["fcf_margin_pct"] == 25.0
    assert result["quality_score"] is not None
    assert result["source"] == "SEC CompanyFacts/XBRL"


def test_fred_macro_uses_levels_and_changes() -> None:
    series = {
        "DGS10": {"summary": {"value": 4.4, "change_5obs": 0.2}},
        "DGS2": {"summary": {"value": 4.0, "change_5obs": 0.1}},
        "BAMLH0A0HYM2": {"summary": {"value": 3.5, "change_5obs": 0.15}},
        "VIXCLS": {"summary": {"value": 22.0, "change_5obs": 3.0}},
    }
    result = _fred_derived(series)
    assert result["yield_curve_10y_2y"] == 0.4
    assert result["macro_risk_score"] is not None
    assert 0 <= result["macro_risk_score"] <= 100


def test_same_symbol_same_trade_date_is_not_counted_twice(tmp_path: Path) -> None:
    store = V6DailyStore(str(tmp_path / "v6.db"))
    engine = V6DailyEngine()
    first = engine.from_analysis_record(_record(1))
    second = engine.from_analysis_record(_record(2))
    assert first is not None and second is not None
    assert store.save_signal(first, engine_version=engine.version)
    assert store.has_signal_key("MSFT", "2026-01-01", engine.version)
    assert not store.save_signal(second, engine_version=engine.version)
    assert store.counts()["signals"] == 1


def _make_stock_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE stock_daily(code TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL)")
        for code, drift in (("MSFT", 1.0), ("SPY", 0.4), ("QQQ", 0.6)):
            for day in range(1, 41):
                close = 100.0 + drift * day
                conn.execute(
                    "INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?)",
                    (code, f"2026-02-{day:02d}", close - 0.2, close + 0.5, close - 0.5, close, 1_000_000 + day * 1000),
                )
        conn.commit()
    finally:
        conn.close()


def test_maturity_uses_each_horizons_own_direction_and_benchmark(tmp_path: Path) -> None:
    stock_db = tmp_path / "stock.db"
    _make_stock_db(stock_db)
    store = V6DailyStore(str(tmp_path / "v6.db"))
    signal = V6DailyEngine().from_analysis_record(_record())
    assert signal is not None
    signal = replace(
        signal,
        baseline_price=101.0,
        horizon_forecasts={
            "5d": {"horizon_days": 5, "score": 80.0, "direction": "bullish", "evidence_coverage": 1.0},
            "10d": {"horizon_days": 10, "score": 20.0, "direction": "bearish", "evidence_coverage": 1.0},
            "20d": {"horizon_days": 20, "score": 50.0, "direction": "neutral", "evidence_coverage": 1.0},
        },
        effective_trade_date="2026-02-01",
    )
    assert store.save_signal(signal, engine_version=V6DailyEngine.version)
    stats = mature_outcomes(store, str(stock_db))
    assert stats["evaluated"] == 3
    with store.connect() as conn:
        rows = conn.execute("SELECT horizon_days,direction_used,directional_hit,excess_vs_spy_pct FROM v6_outcomes ORDER BY horizon_days").fetchall()
    assert [row["direction_used"] for row in rows] == ["bullish", "bearish", "neutral"]
    assert rows[0]["directional_hit"] == 1
    assert rows[1]["directional_hit"] == 0
    assert rows[0]["excess_vs_spy_pct"] is not None


def _series_with_future(multiplier: float) -> dict:
    result = {"MSFT": [], "SPY": [], "QQQ": []}
    for day in range(1, 101):
        date = f"2025-{((day - 1) // 28) + 1:02d}-{((day - 1) % 28) + 1:02d}"
        base = 100.0 + day * 0.3
        future_adjustment = 0.0 if day <= 70 else (day - 70) * multiplier
        result["MSFT"].append({"date": date, "close": base + future_adjustment, "high": base + 1, "low": base - 1, "volume": 1_000_000 + day})
        result["SPY"].append({"date": date, "close": 100 + day * 0.15, "high": 0, "low": 0, "volume": 1})
        result["QQQ"].append({"date": date, "close": 100 + day * 0.20, "high": 0, "low": 0, "volume": 1})
    return result


def test_replay_features_do_not_leak_future_prices() -> None:
    first = replay_series(_series_with_future(0.2), codes=["MSFT"], min_lookback=60)
    second = replay_series(_series_with_future(4.0), codes=["MSFT"], min_lookback=60)
    key_date = _series_with_future(0.2)["MSFT"][70]["date"]
    left = next(item for item in first if item.as_of == key_date and item.horizon_days == 5)
    right = next(item for item in second if item.as_of == key_date and item.horizon_days == 5)
    # Future outcomes can change, but the score at the as-of date cannot.
    assert left.score == right.score
    assert left.direction == right.direction
