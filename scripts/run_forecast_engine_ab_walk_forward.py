from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sqlite3
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HORIZONS = (1, 5, 10, 20)
CONTEXT_HORIZONS = (1, 5, 10, 20, 60)
DEFAULT_CODES = ("MSFT", "GOOGL", "QQQM", "VOO")
DEFAULT_ETFS = {"QQQM", "VOO", "SPY", "QQQ"}
METHOD = "strict-no-lookahead-price-only-forecast-engine-ab-v1"
_BENCH_INDEX_CACHE: dict[int, tuple[list[str], dict[str, int]]] = {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _subtract_years(value: date, years: int) -> date:
    target_year = value.year - int(years)
    try:
        return value.replace(year=target_year)
    except ValueError:
        return value.replace(year=target_year, month=2, day=28)


def _parse_date(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _ret(values: Sequence[float], days: int) -> float | None:
    if days <= 0 or len(values) <= days or values[-days - 1] <= 0:
        return None
    return (values[-1] / values[-days - 1] - 1.0) * 100.0


def _score_return(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return round(_clamp(50.0 + 50.0 * math.tanh(value / scale), 0.0, 100.0), 4)


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return None if not clean else statistics.fmean(clean)


def _ma(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def _annualized_vol(closes: Sequence[float], window: int = 20) -> float | None:
    if len(closes) < window + 1:
        return None
    returns: list[float] = []
    sample = closes[-window - 1 :]
    for left, right in zip(sample, sample[1:]):
        if left > 0:
            returns.append((right / left - 1.0) * 100.0)
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252.0)


def _atr(rows: Sequence[Mapping[str, Any]], index: int, window: int = 14) -> float | None:
    start = max(1, index - window + 1)
    true_ranges: list[float] = []
    for pos in range(start, index + 1):
        high = _finite(rows[pos].get("high"))
        low = _finite(rows[pos].get("low"))
        previous = _finite(rows[pos - 1].get("close"))
        if high is None or low is None or previous is None:
            continue
        true_ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    return None if not true_ranges else statistics.fmean(true_ranges)


def _load_series(stock_db: Path, requested: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    codes = tuple(dict.fromkeys([*(str(code).upper() for code in requested), "SPY", "QQQ"]))
    conn = sqlite3.connect(f"file:{stock_db}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(stock_daily)")}
        fields = [name for name in ("date", "open", "high", "low", "close", "volume") if name in columns]
        if "date" not in fields or "close" not in fields:
            raise SystemExit("stock_daily must contain date and close")
        placeholders = ",".join("?" for _ in codes)
        sql = f"SELECT code,{','.join(fields)} FROM stock_daily WHERE upper(code) IN ({placeholders}) ORDER BY code,date"
        rows = conn.execute(sql, codes).fetchall()
    finally:
        conn.close()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        code = str(item.pop("code") or "").upper()
        if code:
            result[code].append(item)
    missing = [code for code in requested if not result.get(str(code).upper())]
    if missing:
        raise SystemExit("missing requested price history: " + ", ".join(missing))
    return dict(result)


def _bench_map(series: Mapping[str, Sequence[Mapping[str, Any]]], code: str) -> dict[str, Mapping[str, Any]]:
    return {str(row.get("date")): row for row in series.get(code, ()) if row.get("date")}


def _benchmark_trailing(mapping: Mapping[str, Mapping[str, Any]], as_of: str, days: int) -> float | None:
    cache_key = id(mapping)
    cached = _BENCH_INDEX_CACHE.get(cache_key)
    if cached is None:
        dates = sorted(mapping)
        cached = (dates, {value: index for index, value in enumerate(dates)})
        _BENCH_INDEX_CACHE[cache_key] = cached
    dates, positions = cached
    pos = positions.get(as_of)
    if pos is None:
        return None
    if pos < days:
        return None
    current = _finite(mapping[dates[pos]].get("close"))
    previous = _finite(mapping[dates[pos - days]].get("close"))
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _feature_values(
    rows: Sequence[Mapping[str, Any]],
    index: int,
    benchmarks: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, float | None]:
    history = rows[: index + 1]
    closes = [float(row["close"]) for row in history if _finite(row.get("close")) is not None]
    if not closes:
        return {}
    ret5, ret20, ret60 = _ret(closes, 5), _ret(closes, 20), _ret(closes, 60)
    ma20, ma50 = _ma(closes, 20), _ma(closes, 50)
    price = closes[-1]
    trend_parts: list[float] = []
    if ma20 is not None:
        trend_parts.append(70.0 if price >= ma20 else 30.0)
    if ma20 is not None and ma50 is not None:
        trend_parts.append(75.0 if ma20 >= ma50 else 25.0)
    trend_parts.extend(value for value in (_score_return(ret20, 8.0), _score_return(ret60, 16.0)) if value is not None)
    trend = None if not trend_parts else statistics.fmean(trend_parts)
    momentum = _mean((_score_return(ret5, 4.0), _score_return(ret20, 8.0)))

    as_of = str(history[-1].get("date") or "")
    relative: list[float | None] = []
    for benchmark in ("SPY", "QQQ"):
        mapping = benchmarks.get(benchmark) or {}
        for horizon, scale, own_ret in ((20, 6.0, ret20), (60, 12.0, ret60)):
            market_ret = _benchmark_trailing(mapping, as_of, horizon)
            if market_ret is not None and own_ret is not None:
                relative.append(_score_return(own_ret - market_ret, scale))
    relative_strength = _mean(relative)

    recent_volume = [_finite(row.get("volume")) for row in history[-21:]]
    clean_volume = [value for value in recent_volume if value is not None and value >= 0]
    volume_confirmation = None
    if len(clean_volume) >= 6:
        denominator = statistics.fmean(clean_volume[:-1]) if clean_volume[:-1] else 0.0
        if denominator > 0:
            rvol = clean_volume[-1] / denominator
            intensity = math.tanh((rvol - 1.0) / 0.6)
            direction = max(-1.0, min(1.0, (ret5 or 0.0) / 3.0))
            volume_confirmation = _clamp(50.0 + 35.0 * intensity * direction, 0.0, 100.0)

    spy20 = _benchmark_trailing(benchmarks.get("SPY") or {}, as_of, 20)
    spy60 = _benchmark_trailing(benchmarks.get("SPY") or {}, as_of, 60)
    market_regime_score = _mean((_score_return(spy20, 6.0), _score_return(spy60, 12.0)))
    realized = _annualized_vol(closes, 20)
    volatility_risk = None if realized is None else _clamp(100.0 * realized / (realized + 28.0), 0.0, 100.0)
    observed = [trend, momentum, relative_strength, volume_confirmation, market_regime_score, volatility_risk]
    data_quality = 100.0 * sum(value is not None for value in observed) / len(observed)
    return {
        "trend": None if trend is None else round(trend, 4),
        "momentum": None if momentum is None else round(momentum, 4),
        "relative_strength": None if relative_strength is None else round(relative_strength, 4),
        "volume_confirmation": None if volume_confirmation is None else round(volume_confirmation, 4),
        "market_regime": None if market_regime_score is None else round(market_regime_score, 4),
        "volatility_risk": None if volatility_risk is None else round(volatility_risk, 4),
        "data_quality": round(data_quality, 4),
    }


def _market_regime(benchmarks: Mapping[str, Mapping[str, Mapping[str, Any]]], as_of: str) -> str:
    spy = benchmarks.get("SPY") or {}
    ret20 = _benchmark_trailing(spy, as_of, 20)
    ret60 = _benchmark_trailing(spy, as_of, 60)
    if ret20 is not None and ret60 is not None:
        if ret20 > 0 and ret60 > 0:
            return "risk_on"
        if ret20 < 0 and ret60 < 0:
            return "risk_off"
    return "mixed"


def _context(
    rows: Sequence[Mapping[str, Any]],
    index: int,
    benchmarks: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    history = rows[: index + 1]
    closes = [float(row["close"]) for row in history if _finite(row.get("close")) is not None]
    as_of = str(history[-1].get("date") or "")
    blocks: dict[str, dict[str, float]] = {}
    for horizon in CONTEXT_HORIZONS:
        own = _ret(closes, horizon)
        block: dict[str, float] = {}
        if own is not None:
            block["target_return_pct"] = round(own, 6)
        spy = _benchmark_trailing(benchmarks.get("SPY") or {}, as_of, horizon)
        qqq = _benchmark_trailing(benchmarks.get("QQQ") or {}, as_of, horizon)
        if own is not None and spy is not None:
            block["excess_vs_spy_pct"] = round(own - spy, 6)
        if own is not None and qqq is not None:
            block["excess_vs_qqq_pct"] = round(own - qqq, 6)
        blocks[f"{horizon}d"] = block
    realized = _annualized_vol(closes, 20)
    prediction: dict[str, Any] = {"horizons": blocks}
    if realized is not None:
        prediction["realized_vol_20d_pct"] = round(realized, 6)
    return {"prediction_context": prediction, "effective_daily_bar_date": as_of}


def _future_outcome(
    rows: Sequence[Mapping[str, Any]],
    index: int,
    horizon: int,
    benchmarks: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any] | None:
    end_index = index + horizon
    if end_index >= len(rows):
        return None
    start = _finite(rows[index].get("close"))
    end = _finite(rows[end_index].get("close"))
    if start is None or end is None or start <= 0:
        return None
    sample = rows[index + 1 : end_index + 1]
    highs = [_finite(row.get("high")) for row in sample]
    lows = [_finite(row.get("low")) for row in sample]
    clean_highs = [value for value in highs if value is not None]
    clean_lows = [value for value in lows if value is not None]
    realized = (end / start - 1.0) * 100.0
    mfe = None if not clean_highs else (max(clean_highs) / start - 1.0) * 100.0
    mae = None if not clean_lows else (min(clean_lows) / start - 1.0) * 100.0
    as_of = str(rows[index].get("date") or "")
    end_date = str(rows[end_index].get("date") or "")
    spy = benchmarks.get("SPY") or {}
    spy_start = _finite((spy.get(as_of) or {}).get("close"))
    spy_end = _finite((spy.get(end_date) or {}).get("close"))
    excess = None
    if spy_start is not None and spy_end is not None and spy_start > 0:
        excess = realized - (spy_end / spy_start - 1.0) * 100.0
    return {
        "end_trade_date": end_date,
        "return_pct": realized,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "excess_vs_spy_pct": excess,
    }


def _init_history_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path, timeout=30)
    conn.executescript(
        """
        CREATE TABLE v6_forecast_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engine_version TEXT,
            market_regime TEXT,
            effective_trade_date TEXT,
            symbol TEXT,
            instrument_type TEXT
        );
        CREATE TABLE v6_horizon_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            score REAL,
            payload_json TEXT
        );
        CREATE TABLE v6_forecast_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_run_id INTEGER,
            horizon_days INTEGER,
            end_trade_date TEXT,
            return_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            excess_vs_spy_pct REAL
        );
        CREATE INDEX ix_ab_outcome_horizon_end ON v6_forecast_outcomes(horizon_days,end_trade_date);
        """
    )
    conn.commit()
    return conn


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return result if isinstance(result, dict) else {}
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return dict(getattr(value, "__dict__", {}) or {})


def _flush_matured(conn: sqlite3.Connection, pending: list[dict[str, Any]], as_of: str) -> None:
    ready = [item for item in pending if str(item["end_trade_date"]) < as_of]
    if not ready:
        return
    conn.executemany(
        "INSERT INTO v6_forecast_outcomes(forecast_run_id,horizon_days,end_trade_date,return_pct,mfe_pct,mae_pct,excess_vs_spy_pct) VALUES (?,?,?,?,?,?,?)",
        [
            (
                item["forecast_run_id"], item["horizon_days"], item["end_trade_date"], item["return_pct"],
                item["mfe_pct"], item["mae_pct"], item["excess_vs_spy_pct"],
            )
            for item in ready
        ],
    )
    conn.commit()
    ready_ids = {id(item) for item in ready}
    pending[:] = [item for item in pending if id(item) not in ready_ids]


def _log_loss(probability: float, outcome: int) -> float:
    p = _clamp(probability, 1e-9, 1.0 - 1e-9)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p))


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "samples": 0,
            "directional_accuracy_pct": None,
            "non_neutral_signal_accuracy_pct": None,
            "non_neutral_samples": 0,
            "neutral_rate_pct": None,
            "brier_score": None,
            "log_loss": None,
            "return_mae_pct": None,
            "mature_share_pct": None,
        }
    binary_hits = [int((float(row["probability_up"]) >= 0.5) == (float(row["realized_return_pct"]) > 0.0)) for row in rows]
    signal_rows = [row for row in rows if str(row.get("direction")) in {"bullish", "bearish"}]
    signal_hits = [
        int((str(row["direction"]) == "bullish" and float(row["realized_return_pct"]) > 0.0)
            or (str(row["direction"]) == "bearish" and float(row["realized_return_pct"]) < 0.0))
        for row in signal_rows
    ]
    outcomes = [int(float(row["realized_return_pct"]) > 0.0) for row in rows]
    brier = [(float(row["probability_up"]) - outcome) ** 2 for row, outcome in zip(rows, outcomes)]
    losses = [_log_loss(float(row["probability_up"]), outcome) for row, outcome in zip(rows, outcomes)]
    return_errors = [abs(float(row["expected_return_pct"]) - float(row["realized_return_pct"])) for row in rows]
    mature = [str(row.get("calibration_status")) == "mature" for row in rows]
    return {
        "samples": len(rows),
        "directional_accuracy_pct": round(100.0 * sum(binary_hits) / len(binary_hits), 3),
        "non_neutral_signal_accuracy_pct": None if not signal_hits else round(100.0 * sum(signal_hits) / len(signal_hits), 3),
        "non_neutral_samples": len(signal_rows),
        "neutral_rate_pct": round(100.0 * (len(rows) - len(signal_rows)) / len(rows), 3),
        "brier_score": round(statistics.fmean(brier), 6),
        "log_loss": round(statistics.fmean(losses), 6),
        "return_mae_pct": round(statistics.fmean(return_errors), 6),
        "mature_share_pct": round(100.0 * sum(mature) / len(mature), 3),
    }


def _variant_report(observations: Sequence[Mapping[str, Any]], *, label: str, engine_version: str, years: int) -> dict[str, Any]:
    by_horizon: dict[str, Any] = {}
    for horizon in HORIZONS:
        group = [row for row in observations if int(row["horizon_days"]) == horizon]
        by_horizon[f"{horizon}d"] = _summary(group)
    by_instrument: dict[str, Any] = {}
    for instrument in sorted({str(row["instrument_type"]) for row in observations}):
        by_instrument[instrument] = {
            f"{h}d": _summary([row for row in observations if row["instrument_type"] == instrument and int(row["horizon_days"]) == h])
            for h in HORIZONS
        }
    by_symbol: dict[str, Any] = {}
    for symbol in sorted({str(row["symbol"]) for row in observations}):
        by_symbol[symbol] = {
            f"{h}d": _summary([row for row in observations if row["symbol"] == symbol and int(row["horizon_days"]) == h])
            for h in HORIZONS
        }
    return {
        "label": label,
        "engine_version": engine_version,
        "method": METHOD,
        "history_years": years,
        "observations": len(observations),
        "by_horizon": by_horizon,
        "by_instrument_type": by_instrument,
        "by_symbol": by_symbol,
    }


def _run_variant(args: argparse.Namespace) -> None:
    root = Path(args.engine_root).resolve()
    if not (root / "src" / "forecasting" / "engine.py").is_file():
        raise SystemExit(f"invalid engine root: {root}")
    sys.path.insert(0, str(root))
    from src.alpha_engine.models import AlphaFeatures  # type: ignore
    from src.forecasting.engine import V7ForecastEngine  # type: ignore

    requested = tuple(dict.fromkeys(code.strip().upper() for code in args.codes.split(",") if code.strip()))
    series = _load_series(Path(args.stock_db).resolve(), requested)
    benchmarks = {"SPY": _bench_map(series, "SPY"), "QQQ": _bench_map(series, "QQQ")}
    latest_dates = [
        _parse_date(rows[-1].get("date")) for code in requested for rows in [series.get(code, [])] if rows
    ]
    latest = max(value for value in latest_dates if value is not None)
    start_date = _subtract_years(latest, args.years)
    history_path = Path(args.history_db).resolve()
    conn = _init_history_db(history_path)
    engine = V7ForecastEngine(history_db_path=str(history_path))
    pending: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    tasks: list[tuple[str, str, int]] = []
    for code in requested:
        rows = series[code]
        for index, row in enumerate(rows):
            as_of = str(row.get("date") or "")
            parsed = _parse_date(as_of)
            if parsed is None or parsed < start_date or index < args.min_lookback:
                continue
            tasks.append((as_of, code, index))
    tasks.sort(key=lambda item: (item[0], item[1]))

    current_date = ""
    for as_of, code, index in tasks:
        if as_of != current_date:
            if current_date:
                conn.commit()
            _flush_matured(conn, pending, as_of)
            current_date = as_of
        rows = series[code]
        price = _finite(rows[index].get("close"))
        if price is None or price <= 0:
            continue
        feature_kwargs = _feature_values(rows, index, benchmarks)
        features = AlphaFeatures(**feature_kwargs)
        instrument = "ETF" if code in DEFAULT_ETFS else "STOCK"
        regime = _market_regime(benchmarks, as_of)
        bundle = engine.forecast(
            symbol=code,
            instrument_type=instrument,
            effective_trade_date=as_of,
            context=_context(rows, index, benchmarks),
            features=features,
            market_regime=regime,
            atr=_atr(rows, index),
            current_price=price,
        )
        cursor = conn.execute(
            "INSERT INTO v6_forecast_runs(engine_version,market_regime,effective_trade_date,symbol,instrument_type) VALUES (?,?,?,?,?)",
            (str(getattr(engine, "version", "unknown")), regime, as_of, code, instrument),
        )
        run_id = int(cursor.lastrowid)
        for horizon in HORIZONS:
            block = bundle.horizons.get(f"{horizon}d")
            outcome = _future_outcome(rows, index, horizon, benchmarks)
            if block is None or outcome is None:
                continue
            payload = _payload(block)
            probability = _finite(payload.get("probability_up"))
            expected = _finite(payload.get("expected_return_pct"))
            score = _finite(payload.get("score"))
            if probability is None or expected is None:
                continue
            conn.execute(
                "INSERT INTO v6_horizon_forecasts(forecast_run_id,horizon_days,score,payload_json) VALUES (?,?,?,?)",
                (run_id, horizon, score, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
            pending.append({"forecast_run_id": run_id, "horizon_days": horizon, **outcome})
            observations.append(
                {
                    "key": f"{code}|{as_of}|{horizon}",
                    "symbol": code,
                    "instrument_type": instrument,
                    "as_of": as_of,
                    "horizon_days": horizon,
                    "engine_version": str(getattr(engine, "version", "unknown")),
                    "probability_up": round(probability, 8),
                    "expected_return_pct": round(expected, 8),
                    "direction": str(payload.get("direction") or "neutral"),
                    "calibration_status": str(payload.get("calibration_status") or "prior_only"),
                    "calibration_samples": int(payload.get("calibration_samples") or 0),
                    "realized_return_pct": round(float(outcome["return_pct"]), 8),
                    "end_trade_date": outcome["end_trade_date"],
                }
            )
    conn.commit()
    conn.close()
    engine_version = str(getattr(engine, "version", "unknown"))
    output = {
        "report": _variant_report(observations, label=args.label, engine_version=engine_version, years=args.years),
        "observations": observations,
        "integrity": {
            "input_reconstruction": "price-only deterministic features/context from rows at or before as_of",
            "history_visibility": "only outcomes with end_trade_date < current as_of are flushed into calibration DB",
            "future_outcomes_used_for_scoring_only": True,
            "requested_symbols": list(requested),
            "start_date": start_date.isoformat(),
            "end_date": latest.isoformat(),
        },
    }
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"{args.label}: engine={engine_version} observations={len(observations)} output={target}")


def _metric_delta(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"samples": int(new.get("samples") or 0)}
    for key in ("directional_accuracy_pct", "non_neutral_signal_accuracy_pct", "brier_score", "log_loss", "return_mae_pct", "mature_share_pct"):
        left, right = _finite(old.get(key)), _finite(new.get(key))
        result[key] = None if left is None or right is None else round(right - left, 6)
    return result


def _paired_comparison(old_payload: Mapping[str, Any], new_payload: Mapping[str, Any]) -> dict[str, Any]:
    old_obs = {str(row["key"]): row for row in old_payload.get("observations", [])}
    new_obs = {str(row["key"]): row for row in new_payload.get("observations", [])}
    old_keys, new_keys = set(old_obs), set(new_obs)
    if old_keys != new_keys:
        missing_old = sorted(new_keys - old_keys)[:20]
        missing_new = sorted(old_keys - new_keys)[:20]
        raise SystemExit(f"paired AB mismatch old_missing={missing_old} new_missing={missing_new}")
    old_report = old_payload["report"]
    new_report = new_payload["report"]
    by_horizon = {
        key: _metric_delta(old_report["by_horizon"][key], new_report["by_horizon"][key])
        for key in ("1d", "5d", "10d", "20d")
    }
    by_instrument: dict[str, Any] = {}
    instruments = sorted(set(old_report["by_instrument_type"]) | set(new_report["by_instrument_type"]))
    for instrument in instruments:
        by_instrument[instrument] = {
            key: _metric_delta(
                (old_report["by_instrument_type"].get(instrument) or {}).get(key) or {},
                (new_report["by_instrument_type"].get(instrument) or {}).get(key) or {},
            )
            for key in ("1d", "5d", "10d", "20d")
        }
    by_symbol: dict[str, Any] = {}
    symbols = sorted(set(old_report["by_symbol"]) | set(new_report["by_symbol"]))
    for symbol in symbols:
        by_symbol[symbol] = {
            key: _metric_delta(
                (old_report["by_symbol"].get(symbol) or {}).get(key) or {},
                (new_report["by_symbol"].get(symbol) or {}).get(key) or {},
            )
            for key in ("1d", "5d", "10d", "20d")
        }
    return {
        "method": METHOD,
        "paired_observations": len(old_keys),
        "paired_keys_equal": True,
        "old_engine_version": old_report.get("engine_version"),
        "new_engine_version": new_report.get("engine_version"),
        "delta_semantics": {
            "directional_accuracy_pct": "positive is better",
            "non_neutral_signal_accuracy_pct": "positive is better",
            "brier_score": "negative is better",
            "log_loss": "negative is better",
            "return_mae_pct": "negative is better",
            "mature_share_pct": "descriptive only",
        },
        "by_horizon": by_horizon,
        "by_instrument_type": by_instrument,
        "by_symbol": by_symbol,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    number = _finite(value)
    return "N/A" if number is None else f"{number:.{digits}f}"


def _markdown(old: Mapping[str, Any], new: Mapping[str, Any], comparison: Mapping[str, Any]) -> str:
    lines = [
        "# V7.3 Forecast Engine AB Walk-Forward Backtest",
        "",
        "> Actual V7.1 and V7.3 Forecast Engine implementations are replayed on identical price-only deterministic as-of inputs. Each engine owns a separate calibration DB; an outcome becomes visible only when `end_trade_date < as_of`.",
        "",
        f"- Old: `{old['report'].get('engine_version')}`",
        f"- New: `{new['report'].get('engine_version')}`",
        f"- Paired observations: **{comparison.get('paired_observations', 0)}**",
        "- Metrics: binary directional accuracy (P(up) >= 50%), non-neutral signal accuracy, Brier, log loss, return MAE.",
        "- Scope limitation: this is a deterministic OHLCV reconstruction of Forecast Engine inputs, not a historical replay of unavailable news/LLM snapshots.",
        "",
        "## Overall by horizon",
        "",
        "| Horizon | Model | N | Dir Acc | Signal Acc | Brier | Log loss | Return MAE | Mature share |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for horizon in ("1d", "5d", "10d", "20d"):
        for label, payload in (("V7.1", old), ("V7.3", new)):
            metric = payload["report"]["by_horizon"][horizon]
            lines.append(
                f"| {horizon} | {label} | {metric['samples']} | {_fmt(metric['directional_accuracy_pct'])}% | {_fmt(metric['non_neutral_signal_accuracy_pct'])}% | {_fmt(metric['brier_score'], 6)} | {_fmt(metric['log_loss'], 6)} | {_fmt(metric['return_mae_pct'], 4)}% | {_fmt(metric['mature_share_pct'])}% |"
            )
        delta = comparison["by_horizon"][horizon]
        lines.append(
            f"| {horizon} | Δ V7.3−V7.1 | {delta['samples']} | {_fmt(delta['directional_accuracy_pct'])}pp | {_fmt(delta['non_neutral_signal_accuracy_pct'])}pp | {_fmt(delta['brier_score'], 6)} | {_fmt(delta['log_loss'], 6)} | {_fmt(delta['return_mae_pct'], 4)}% | {_fmt(delta['mature_share_pct'])}pp |"
        )
    lines.extend(["", "## Stock / ETF split", ""])
    lines.append("| Type | Horizon | Δ Dir Acc | Δ Brier | Δ Log loss | Δ Return MAE |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for instrument, horizons in comparison["by_instrument_type"].items():
        for horizon in ("1d", "5d", "10d", "20d"):
            delta = horizons[horizon]
            lines.append(
                f"| {instrument} | {horizon} | {_fmt(delta['directional_accuracy_pct'])}pp | {_fmt(delta['brier_score'], 6)} | {_fmt(delta['log_loss'], 6)} | {_fmt(delta['return_mae_pct'], 4)}% |"
            )
    return "\n".join(lines) + "\n"


def _run_ab(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    old_json = output_dir / "v71.json"
    new_json = output_dir / "v73.json"
    common = [
        "--variant-worker",
        "--stock-db", str(Path(args.stock_db).resolve()),
        "--codes", args.codes,
        "--years", str(args.years),
        "--min-lookback", str(args.min_lookback),
    ]
    script = Path(__file__).resolve()
    commands = [
        [sys.executable, str(script), *common, "--engine-root", str(Path(args.old_root).resolve()), "--history-db", str(output_dir / "v71_history.db"), "--output", str(old_json), "--label", "V7.1"],
        [sys.executable, str(script), *common, "--engine-root", str(Path(args.new_root).resolve()), "--history-db", str(output_dir / "v73_history.db"), "--output", str(new_json), "--label", "V7.3"],
    ]
    for command in commands:
        subprocess.run(command, check=True)
    old = json.loads(old_json.read_text(encoding="utf-8"))
    new = json.loads(new_json.read_text(encoding="utf-8"))
    comparison = _paired_comparison(old, new)
    combined = {
        "method": METHOD,
        "old": old["report"],
        "new": new["report"],
        "comparison": comparison,
        "integrity": {
            "paired_keys_equal": True,
            "old_history_db": "separate",
            "new_history_db": "separate",
            "outcome_visibility_rule": "end_trade_date < as_of",
            "same_stock_db": str(Path(args.stock_db).resolve()),
            "same_codes": args.codes,
            "history_years": args.years,
        },
    }
    (output_dir / "forecast_engine_ab.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    markdown = _markdown(old, new, comparison)
    (output_dir / "forecast_engine_ab.md").write_text(markdown, encoding="utf-8")
    print(markdown)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict no-lookahead V7.1 vs V7.3 Forecast Engine AB walk-forward replay")
    parser.add_argument("--variant-worker", action="store_true")
    parser.add_argument("--stock-db", required=True)
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--min-lookback", type=int, default=80)
    parser.add_argument("--old-root")
    parser.add_argument("--new-root")
    parser.add_argument("--output-dir", default="backtest_artifacts/forecast_engine_ab")
    parser.add_argument("--engine-root")
    parser.add_argument("--history-db")
    parser.add_argument("--output")
    parser.add_argument("--label")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.variant_worker:
        for required in ("engine_root", "history_db", "output", "label"):
            if not getattr(args, required):
                raise SystemExit(f"--{required.replace('_', '-')} is required in worker mode")
        _run_variant(args)
        return
    if not args.old_root or not args.new_root:
        raise SystemExit("--old-root and --new-root are required")
    _run_ab(args)


if __name__ == "__main__":
    main()
