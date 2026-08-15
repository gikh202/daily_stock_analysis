from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from scripts.us_open_research_ledger import (
    _finite,
    _normalize_frame,
    _parse_dt,
    export_summary,
    record_signal,
    settle_pending,
)

NY = ZoneInfo("America/New_York")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def fetch_recent_history(symbol: str) -> Any:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        period="5d",
        interval="1m",
        auto_adjust=False,
        prepost=False,
        actions=False,
    )
    frame = _normalize_frame(frame)
    if frame is None or frame.empty:
        raise RuntimeError(f"{symbol}: no recent 1m bars")
    return frame


def reconstruct_snapshot_from_frame(
    *,
    symbol: str,
    decision: Mapping[str, Any],
    frame: Any,
) -> dict[str, Any]:
    bar_time = _parse_dt(decision.get("source_last_bar_time"))
    current_price = _finite(decision.get("current_price"))
    if bar_time is None or current_price is None or current_price <= 0:
        raise ValueError(f"{symbol}: decision lacks executable bar timestamp/price")
    frame = _normalize_frame(frame)
    session = frame[(frame.index.date == bar_time.date()) & (frame.index <= bar_time)]
    session = session.between_time("09:30", "16:00")
    if session.empty:
        raise ValueError(f"{symbol}: no session bars through {bar_time.isoformat()}")
    opening = session.between_time("09:30", "09:44")
    if len(opening) < 5:
        raise ValueError(f"{symbol}: opening window incomplete ({len(opening)} bars)")

    session_open = _finite(session.iloc[0].get("Open"))
    if session_open is None or session_open <= 0:
        raise ValueError(f"{symbol}: invalid session open")
    current_opening_volume = float(opening["Volume"].fillna(0).sum())
    prior_volumes: list[float] = []
    prior_dates = sorted({item for item in frame.index.date if item < bar_time.date()}, reverse=True)
    for prior_date in prior_dates[:4]:
        prior = frame[frame.index.date == prior_date].between_time("09:30", "09:44")
        if len(prior) < 10:
            continue
        volume = _finite(prior["Volume"].fillna(0).sum())
        if volume is not None and volume > 0:
            prior_volumes.append(volume)
    recent_median = median(prior_volumes) if prior_volumes else None
    ratio = _finite(decision.get("volume_ratio"))
    if ratio is None and recent_median is not None and recent_median > 0:
        ratio = current_opening_volume / recent_median

    return {
        "symbol": symbol,
        "current_price": current_price,
        "session_open": session_open,
        "session_high": float(session["High"].max()),
        "session_low": float(session["Low"].min()),
        "opening_15m_high": float(opening["High"].max()),
        "opening_15m_low": float(opening["Low"].min()),
        "return_from_open_pct": (
            _finite(decision.get("return_from_open_pct"))
            if _finite(decision.get("return_from_open_pct")) is not None
            else (current_price / session_open - 1.0) * 100.0
        ),
        "opening_15m_volume": current_opening_volume,
        "recent_opening_volume_median": recent_median,
        "volume_ratio": ratio,
        "bar_count": int(len(session)),
        "last_bar_time": bar_time.isoformat(),
    }


def capture(
    *,
    confirmation_json: str | Path,
    v6_payload: str | Path,
    db: str | Path,
    summary_output: str | Path | None = None,
) -> dict[str, Any]:
    confirmation = json.loads(Path(confirmation_json).read_text(encoding="utf-8"))
    v6 = json.loads(Path(v6_payload).read_text(encoding="utf-8"))
    generated_at = _parse_dt(confirmation.get("generated_at")) or datetime.now(NY)
    policy_version = str(confirmation.get("policy_version") or confirmation.get("version") or "unknown")
    source_run_id = str(confirmation.get("source_run_id") or "") or None

    packets = {}
    final = _mapping(v6.get("final_decisions"))
    for packet in final.get("packets") or []:
        if not isinstance(packet, Mapping):
            continue
        identity = _mapping(packet.get("identity"))
        symbol = str(identity.get("symbol") or "").strip().upper()
        if symbol:
            packets[symbol] = dict(packet)

    settle_result = settle_pending(db, as_of_date=generated_at.date())
    captured = 0
    existing = 0
    failed = 0
    errors: list[str] = []
    for raw in confirmation.get("decisions") or []:
        if not isinstance(raw, Mapping):
            continue
        decision = dict(raw)
        symbol = str(decision.get("symbol") or "").strip().upper()
        packet = packets.get(symbol)
        if not symbol or packet is None:
            failed += 1
            errors.append(f"{symbol or '?'}: missing prior packet")
            continue
        if decision.get("current_price") is None or not decision.get("source_last_bar_time"):
            # DATA_UNAVAILABLE has no executable snapshot. Keep it in the email,
            # but it cannot become a market-outcome research observation.
            continue
        try:
            frame = fetch_recent_history(symbol)
            snapshot = reconstruct_snapshot_from_frame(symbol=symbol, decision=decision, frame=frame)
            inserted = record_signal(
                db,
                packet=packet,
                snapshot=snapshot,
                decision=decision,
                evaluated_at=generated_at,
                policy_version=policy_version,
                source_run_id=source_run_id,
            )
            if inserted:
                captured += 1
            else:
                existing += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{symbol}: {type(exc).__name__}: {exc}")

    summary = export_summary(db, summary_output) if summary_output else None
    return {
        "captured": captured,
        "existing": existing,
        "failed": failed,
        "errors": errors[:20],
        "settlement": settle_result,
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture live US-open confirmation into persistent research ledger")
    parser.add_argument("--confirmation-json", required=True)
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--summary-output", default="open_confirmation_reports/us_open_research_summary.json")
    args = parser.parse_args()
    result = capture(
        confirmation_json=args.confirmation_json,
        v6_payload=args.v6_payload,
        db=args.db,
        summary_output=args.summary_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    # Research capture must never trigger a second user email via schedule retry.
    # Partial failures remain visible in the uploaded summary/artifact and retry
    # naturally on the next trading day.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
