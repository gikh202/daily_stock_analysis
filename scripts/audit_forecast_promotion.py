from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.forecasting.history import ForecastHistory

HORIZONS = (1, 5, 10, 20)


def _latest_universe(db_path: str | Path) -> list[dict[str, str]]:
    path = Path(db_path)
    if not path.is_file():
        raise RuntimeError(f"forecast database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(v6_forecast_runs)")
        }
        required = {
            "symbol",
            "instrument_type",
            "market_regime",
            "effective_trade_date",
        }
        if not required.issubset(columns):
            missing = sorted(required - columns)
            raise RuntimeError(
                f"v6_forecast_runs missing audit columns: {missing}"
            )
        rows = conn.execute(
            """
            SELECT symbol, instrument_type, market_regime, effective_trade_date
            FROM v6_forecast_runs
            WHERE symbol IS NOT NULL
              AND trim(symbol) <> ''
              AND date(effective_trade_date) IS NOT NULL
            ORDER BY date(effective_trade_date) DESC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row["symbol"] or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(
            {
                "symbol": symbol,
                "instrument_type": (
                    str(row["instrument_type"] or "STOCK").strip().upper()
                    or "STOCK"
                ),
                "regime": (
                    str(row["market_regime"] or "unknown").strip().lower()
                    or "unknown"
                ),
                "latest_effective_trade_date": str(
                    row["effective_trade_date"] or ""
                )[:10],
            }
        )
    return result


def _pct(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}%}"
    except (TypeError, ValueError):
        return "N/A"


def _num(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def build_audit(
    db_path: str | Path,
    *,
    as_of_date: str,
    symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    history = ForecastHistory(str(db_path))
    universe = _latest_universe(db_path)
    requested = {
        str(symbol).strip().upper()
        for symbol in (symbols or ())
        if str(symbol).strip()
    }
    if requested:
        universe = [
            item for item in universe if item["symbol"] in requested
        ]

    rows: list[dict[str, Any]] = []
    for item in universe:
        for horizon in HORIZONS:
            selection = history.select_champion(
                as_of_date=as_of_date,
                horizon_days=horizon,
                regime=item["regime"],
                symbol=item["symbol"],
                instrument_type=item["instrument_type"],
            )
            champion = selection.get("champion_metrics") or {}
            challenger = selection.get("challenger_metrics") or {}
            rows.append(
                {
                    "symbol": item["symbol"],
                    "instrument_type": item["instrument_type"],
                    "regime": item["regime"],
                    "horizon_days": horizon,
                    "status": selection.get("status"),
                    "selected_model": selection.get("champion_model"),
                    "evaluation_scope": selection.get("evaluation_scope"),
                    "paired_samples": selection.get("paired_samples"),
                    "majority_baseline_accuracy": selection.get(
                        "majority_baseline_accuracy"
                    ),
                    "inverse_challenger_directional_accuracy": selection.get(
                        "inverse_challenger_directional_accuracy"
                    ),
                    "champion_directional_accuracy": champion.get(
                        "directional_accuracy"
                    ),
                    "challenger_directional_accuracy": challenger.get(
                        "directional_accuracy"
                    ),
                    "champion_direction_skill": champion.get(
                        "direction_skill"
                    ),
                    "challenger_direction_skill": challenger.get(
                        "direction_skill"
                    ),
                    "champion_brier_score": champion.get("brier_score"),
                    "challenger_brier_score": challenger.get("brier_score"),
                    "champion_log_loss": champion.get("log_loss"),
                    "challenger_log_loss": challenger.get("log_loss"),
                    "champion_directional_alpha_pct": champion.get(
                        "directional_alpha_pct"
                    ),
                    "challenger_directional_alpha_pct": challenger.get(
                        "directional_alpha_pct"
                    ),
                    "promotion_gates": dict(
                        selection.get("promotion_gates") or {}
                    ),
                    "promotion_failures": list(
                        selection.get("promotion_failures") or []
                    ),
                    "promotion_gate_version": selection.get(
                        "promotion_gate_version"
                    ),
                }
            )

    promoted = [
        row
        for row in rows
        if row.get("status") == "promoted"
    ]
    return {
        "version": "forecast-promotion-audit-v8.1",
        "as_of_date": as_of_date,
        "database": str(db_path),
        "symbols": [item["symbol"] for item in universe],
        "horizons": list(HORIZONS),
        "summary": {
            "symbol_count": len(universe),
            "evaluations": len(rows),
            "promoted_evaluations": len(promoted),
            "observing_evaluations": sum(
                row.get("status") == "observing" for row in rows
            ),
            "cold_start_evaluations": sum(
                row.get("status") == "cold_start" for row in rows
            ),
        },
        "rows": rows,
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("rows") or []
    lines = [
        "# Forecast Promotion Audit",
        "",
        "> Challenger promotion is evaluated independently by symbol × horizon. "
        "Pooled fallback history is observation-only and cannot promote a "
        "symbol-specific model.",
        "",
        f"- As of: **{payload.get('as_of_date')}**",
        f"- Symbols: **{summary.get('symbol_count', 0)}**",
        f"- Evaluations: **{summary.get('evaluations', 0)}**",
        f"- Promoted: **{summary.get('promoted_evaluations', 0)}**",
        "",
        "| Symbol | H | Status | Scope | N | Majority | Champ Acc | Chall Acc | "
        "Chall Skill | Inverse | ΔBrier | ΔLogLoss | Champ α | Chall α | "
        "Failed gates |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        cb = row.get("champion_brier_score")
        xb = row.get("challenger_brier_score")
        cl = row.get("champion_log_loss")
        xl = row.get("challenger_log_loss")
        delta_brier = (
            None if cb is None or xb is None else float(xb) - float(cb)
        )
        delta_logloss = (
            None if cl is None or xl is None else float(xl) - float(cl)
        )
        failures = ", ".join(row.get("promotion_failures") or []) or "—"
        lines.append(
            f"| {row.get('symbol')} | {row.get('horizon_days')}D | "
            f"{row.get('status')} | {row.get('evaluation_scope')} | "
            f"{row.get('paired_samples') or 0} | "
            f"{_pct(row.get('majority_baseline_accuracy'))} | "
            f"{_pct(row.get('champion_directional_accuracy'))} | "
            f"{_pct(row.get('challenger_directional_accuracy'))} | "
            f"{_pct(row.get('challenger_direction_skill'))} | "
            f"{_pct(row.get('inverse_challenger_directional_accuracy'))} | "
            f"{_num(delta_brier, 6)} | {_num(delta_logloss, 6)} | "
            f"{_num(row.get('champion_directional_alpha_pct'), 3)}% | "
            f"{_num(row.get('challenger_directional_alpha_pct'), 3)}% | "
            f"{failures} |"
        )
    lines += [
        "",
        "Promotion requires all gates: symbol-specific scope, sample floor, "
        "direction accuracy ≥52%, direction Skill ≥+2pp, better direction than "
        "Champion and inverse control, better Brier and Log Loss, sufficient "
        "alpha samples, positive directional alpha, and improved directional alpha.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit strict symbol × horizon Forecast Challenger promotion gates"
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated symbol filter",
    )
    parser.add_argument(
        "--json-output",
        default="forecast_promotion_audit.json",
    )
    parser.add_argument(
        "--markdown-output",
        default="forecast_promotion_audit.md",
    )
    args = parser.parse_args()
    symbols = [
        item.strip().upper()
        for item in args.symbols.split(",")
        if item.strip()
    ]
    payload = build_audit(
        args.db,
        as_of_date=args.as_of,
        symbols=symbols or None,
    )
    Path(args.json_output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    Path(args.markdown_output).write_text(
        render_markdown(payload),
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
