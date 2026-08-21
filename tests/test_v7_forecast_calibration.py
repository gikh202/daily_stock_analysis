from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.alpha_engine.models import AlphaFeatures
from src.forecasting.decision import ForecastDecisionPolicy
from src.forecasting.engine import V7ForecastEngine
from src.forecasting.history import CalibrationProfile, ForecastHistory
from src.forecasting.models import ForecastBundle, ForecastHorizon


def _db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE v6_forecast_runs (
            id INTEGER PRIMARY KEY,
            engine_version TEXT,
            market_regime TEXT,
            effective_trade_date TEXT
        );
        CREATE TABLE v6_horizon_forecasts (
            id INTEGER PRIMARY KEY,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            score REAL,
            payload_json TEXT
        );
        CREATE TABLE v6_forecast_outcomes (
            id INTEGER PRIMARY KEY,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            end_trade_date TEXT,
            return_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            excess_vs_spy_pct REAL
        );
        """
    )
    for idx in range(1, 41):
        positive = idx <= 30
        champion_p = 0.75 if positive else 0.25
        challenger_p = 0.65 if positive else 0.35
        ret = 2.0 if positive else -1.5
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",
            (idx, "v7-test", "risk_on", f"2026-01-{(idx % 20) + 1:02d}"),
        )
        conn.execute(
            """
            INSERT INTO v6_horizon_forecasts(
                forecast_run_id,horizon_days,score,payload_json
            ) VALUES (?,?,?,?)
            """,
            (
                idx,
                5,
                champion_p * 100,
                json.dumps(
                    {
                        "probability_up": champion_p,
                        "challenger_probability_up": challenger_p,
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO v6_forecast_outcomes(
                forecast_run_id,horizon_days,end_trade_date,return_pct,
                mfe_pct,mae_pct,excess_vs_spy_pct
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                idx,
                5,
                f"2026-02-{(idx % 20) + 1:02d}",
                ret,
                max(ret, 3.0),
                min(ret, -1.0),
                ret - 0.5,
            ),
        )
    conn.commit()
    conn.close()


def test_calibration_uses_only_strictly_prior_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(
        str(path),
        minimum_samples=20,
        minimum_regime_samples=10,
    )
    profile = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.75,
        regime="risk_on",
    )
    assert profile.samples >= 20
    assert profile.status == "mature"
    assert 0.5 < profile.probability_up < 0.95
    assert profile.brier_score is not None
    assert profile.log_loss is not None
    assert profile.ece is not None

    no_future = history.calibration(
        as_of_date="2026-01-15",
        horizon_days=5,
        raw_probability_up=0.75,
        regime="risk_on",
    )
    assert no_future.samples == 0
    assert no_future.status == "prior_only"


def test_missing_or_invalid_as_of_date_never_unlocks_history(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(str(path))

    for as_of in (None, "", "not-a-date"):
        profile = history.calibration(
            as_of_date=as_of,
            horizon_days=5,
            raw_probability_up=0.61,
            regime="risk_on",
        )
        assert profile.samples == 0
        assert profile.status == "prior_only"
        assert profile.probability_up == 0.61
        assert profile.source == "missing_as_of"

        selection = history.select_champion(
            as_of_date=as_of,
            horizon_days=5,
            regime="risk_on",
        )
        assert selection["status"] == "cold_start"
        assert selection["champion_metrics"]["samples"] == 0
        assert selection["challenger_metrics"]["samples"] == 0


def test_challenger_calibration_uses_its_own_probability_series(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(
        str(path),
        minimum_samples=20,
        minimum_regime_samples=10,
    )

    champion = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.65,
        regime="risk_on",
        probability_key="probability_up",
    )
    challenger = history.calibration(
        as_of_date="2026-03-01",
        horizon_days=5,
        raw_probability_up=0.65,
        regime="risk_on",
        probability_key="challenger_probability_up",
    )

    assert champion.source.endswith(":probability_up")
    assert challenger.source.endswith(":challenger_probability_up")
    assert challenger.samples == 30
    assert champion.samples == 40
    assert challenger.probability_up != champion.probability_up


def test_challenger_cannot_promote_without_forward_sample_floor(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    history = ForecastHistory(str(path))
    selection = history.select_champion(
        as_of_date="2026-03-01",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=200,
    )
    assert selection["champion_model"] == "calibrated_ensemble"
    assert selection["status"] in {"observing", "cold_start"}


def test_history_rows_reject_missing_or_invalid_source_dates(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    conn = sqlite3.connect(path)
    for idx, effective in ((1001, None), (1002, "not-a-date")):
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",
            (idx, "v7-test", "risk_on", effective),
        )
        conn.execute(
            "INSERT INTO v6_horizon_forecasts(forecast_run_id,horizon_days,score,payload_json) VALUES (?,?,?,?)",
            (
                idx,
                5,
                90.0,
                json.dumps(
                    {
                        "probability_up": 0.9,
                        "challenger_probability_up": 0.9,
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,end_trade_date,return_pct,mfe_pct,mae_pct,excess_vs_spy_pct) VALUES (?,?,?,?,?,?,?)",
            (idx, 5, "2026-02-10", 50.0, 50.0, -1.0, 49.0),
        )
    conn.commit()
    conn.close()
    rows = ForecastHistory(str(path))._rows(
        as_of_date="2026-03-01", horizon_days=5
    )
    assert len(rows) == 40
    assert all(row["effective_trade_date"] for row in rows)


def test_promotion_metrics_use_only_paired_forward_predictions(tmp_path: Path) -> None:
    path = tmp_path / "forecast.db"
    _db(path)
    conn = sqlite3.connect(path)
    for idx in range(41, 141):
        conn.execute(
            "INSERT INTO v6_forecast_runs VALUES (?,?,?,?)",
            (idx, "legacy-v6", "risk_on", "2026-01-10"),
        )
        conn.execute(
            "INSERT INTO v6_horizon_forecasts(forecast_run_id,horizon_days,score,payload_json) VALUES (?,?,?,?)",
            (idx, 5, 60.0, json.dumps({"probability_up": 0.6})),
        )
        conn.execute(
            "INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,end_trade_date,return_pct,mfe_pct,mae_pct,excess_vs_spy_pct) VALUES (?,?,?,?,?,?,?)",
            (idx, 5, "2026-02-10", 1.0, 2.0, -1.0, 0.5),
        )
    conn.commit()
    conn.close()
    selection = ForecastHistory(str(path)).select_champion(
        as_of_date="2026-03-01",
        horizon_days=5,
        regime="risk_on",
        min_promotion_samples=20,
    )
    assert selection["evaluation_basis"] == "paired_forward_only"
    assert selection["paired_samples"] == 40
    assert selection["champion_metrics"]["samples"] == 40
    assert selection["challenger_metrics"]["samples"] == 40


class _PerHorizonHistory:
    available = True

    def select_champion(self, *, horizon_days, **kwargs):
        promoted = horizon_days == 5
        return {
            "champion_model": (
                "momentum_challenger" if promoted else "calibrated_ensemble"
            ),
            "challenger_model": (
                "calibrated_ensemble" if promoted else "momentum_challenger"
            ),
            "status": "promoted" if promoted else "observing",
            "paired_samples": 250,
        }

    def calibration(self, *, raw_probability_up, probability_key, **kwargs):
        return CalibrationProfile(
            "mature",
            250,
            250,
            raw_probability_up,
            1.0,
            0.5,
            2.0,
            -1.0,
            -1.0,
            1.0,
            3.0,
            0.20,
            0.50,
            0.05,
            probability_key,
        )


def test_champion_selection_is_independent_per_horizon() -> None:
    bundle = V7ForecastEngine(history=_PerHorizonHistory()).forecast(
        symbol="TEST",
        instrument_type="STOCK",
        effective_trade_date="2026-08-20",
        context={},
        features=AlphaFeatures(
            trend=70,
            momentum=65,
            relative_strength=60,
            volume_confirmation=60,
            market_regime=60,
        ),
        market_regime="risk_on",
        atr=2.0,
        current_price=100.0,
    )
    assert bundle.horizons["1d"].champion_model == "calibrated_ensemble"
    assert bundle.horizons["5d"].champion_model == "momentum_challenger"
    assert bundle.horizons["10d"].champion_model == "calibrated_ensemble"
    assert bundle.horizons["20d"].champion_model == "calibrated_ensemble"
    assert bundle.champion_model == "momentum_challenger"


def _forecast_horizon(
    days: int, *, status: str = "mature", confidence: float = 0.8
) -> ForecastHorizon:
    return ForecastHorizon(
        horizon_days=days,
        raw_probability_up=0.7,
        probability_up=0.7,
        expected_return_pct=1.2,
        expected_alpha_vs_spy_pct=0.6,
        p10_return_pct=-1.0,
        p50_return_pct=1.2,
        p90_return_pct=3.0,
        expected_mfe_pct=2.0,
        expected_mae_pct=-0.8,
        evidence_coverage=0.9,
        forecast_confidence=confidence,
        calibration_samples=50 if status != "prior_only" else 0,
        calibration_status=status,
        regime="risk_on",
        champion_model="calibrated_ensemble",
        challenger_model="momentum_challenger",
        challenger_probability_up=0.65,
        direction="bullish",
        score=70.0,
    )


def _bundle(*, status: str = "mature", confidence: float = 0.8) -> ForecastBundle:
    horizons = {
        f"{days}d": _forecast_horizon(
            days,
            status=status if days == 5 else "mature",
            confidence=confidence,
        )
        for days in (1, 5, 20)
    }
    return ForecastBundle(
        "TEST",
        "STOCK",
        "2026-08-20",
        "risk_on",
        "test",
        horizons,
        "5d",
        "calibrated_ensemble",
        "momentum_challenger",
        "observing",
        0.9,
    )


def test_prior_only_forecast_cannot_authorize_new_exposure() -> None:
    policy = ForecastDecisionPolicy()
    decision = policy.decide(
        _bundle(status="prior_only"), risk_score=20, opportunity_score=80
    )
    assert decision.decision == "WAIT"
    assert decision.max_position_fraction == 0.0
    assert "probability_not_yet_calibrated" in decision.gates
    plan = policy.build_trade_plan(
        decision,
        risk_score=20,
        current_price=100,
        support=98,
        resistance=106,
        atr=2,
    )
    assert plan["entry_zone"] is None
    assert plan["max_position_pct"] == 0.0


def test_low_confidence_gate_cannot_leave_watch_position_authorized() -> None:
    decision = ForecastDecisionPolicy().decide(
        _bundle(confidence=0.4), risk_score=20, opportunity_score=80
    )
    assert decision.decision == "WAIT"
    assert decision.max_position_fraction == 0.0
    assert "forecast_confidence_below_50pct" in decision.gates
