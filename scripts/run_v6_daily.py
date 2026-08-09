from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.alpha_engine.shadow_store import read_analysis_records
from src.v6_daily.accuracy_lab import (
    DEFAULT_COST_BPS,
    DEFAULT_MAX_HOLDING_BARS,
    DEFAULT_PROMOTION_MIN_SAMPLES,
    run_accuracy_lab,
)
from src.v6_daily.accuracy_report import build_accuracy_unified_report
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.free_sources import fetch_free_context
from src.v6_daily.report import write_daily_report
from src.v6_daily.store import V6DailyStore, mature_outcomes
from src.v6_daily.unified_report import count_v4_structured_records


logger = logging.getLogger("v6_daily")
MAX_CURRENT_EXTERNAL_CONTEXT_AGE_DAYS = 4
MAX_CURRENT_ANALYSIS_AGE_DAYS = 1


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_current_external_context_safe(
    effective_trade_date: str | None,
    analysis_created_at: str | None = None,
) -> bool:
    """Allow current SEC/FRED scoring only for a newly-created forecast.

    The external snapshot fetched by this runner is current, not point-in-time
    historical data. A recent effective market bar alone is insufficient: the
    V4 analysis record itself must also have been created today (or at most one
    calendar day earlier for timezone-boundary runs). This prevents a weekend
    or manual rebuild from retroactively injecting new filings/rates into an
    older forecast whose evaluation window has already begun.
    """
    trade_date = _parse_iso_date(effective_trade_date)
    if trade_date is None:
        return False
    trade_age = (date.today() - trade_date).days
    if trade_age < 0 or trade_age > MAX_CURRENT_EXTERNAL_CONTEXT_AGE_DAYS:
        return False

    if analysis_created_at is None:
        return True
    analysis_date = _parse_iso_date(analysis_created_at)
    if analysis_date is None:
        return False
    analysis_age = (date.today() - analysis_date).days
    return 0 <= analysis_age <= MAX_CURRENT_ANALYSIS_AGE_DAYS


def _canonicalize_provisional(
    provisional: Iterable[Tuple[Dict[str, Any], Any]],
) -> Tuple[list[Tuple[Dict[str, Any], Any]], int]:
    """Keep one deterministic V4 record per symbol/effective trade date.

    Multiple forced V4 runs on the same market date are correlated observations,
    not independent forecast samples. The newest analysis_created_at wins; the
    history id is a deterministic tie-breaker.
    """
    canonical: Dict[tuple[str, str], Tuple[Dict[str, Any], Any]] = {}
    total = 0
    for record, signal in provisional:
        total += 1
        key = (
            str(getattr(signal, "code", "") or "").strip().upper(),
            str(getattr(signal, "effective_trade_date", "") or "").strip(),
        )
        candidate_rank = (
            str(getattr(signal, "analysis_created_at", "") or ""),
            int(getattr(signal, "analysis_history_id", 0) or 0),
        )
        current = canonical.get(key)
        if current is None:
            canonical[key] = (record, signal)
            continue
        current_signal = current[1]
        current_rank = (
            str(getattr(current_signal, "analysis_created_at", "") or ""),
            int(getattr(current_signal, "analysis_history_id", 0) or 0),
        )
        if candidate_rank > current_rank:
            canonical[key] = (record, signal)

    rows = sorted(
        canonical.values(),
        key=lambda pair: (
            str(getattr(pair[1], "effective_trade_date", "") or ""),
            str(getattr(pair[1], "code", "") or ""),
            str(getattr(pair[1], "analysis_created_at", "") or ""),
        ),
    )
    return rows, max(0, total - len(rows))


def _notify(report_path: Path, codes: list[str]) -> Dict[str, Any]:
    try:
        from src.notification import NotificationService

        content = report_path.read_text(encoding="utf-8")
        service = NotificationService()
        if not service.is_available():
            return {"attempted": False, "success": False, "status": "no_channel"}
        success = service.send(
            content,
            email_stock_codes=codes,
            email_send_to_all=True,
            route_type="report",
            severity="info",
            dedup_key=f"v6-unified-daily-{datetime.now().strftime('%Y%m%d')}",
        )
        return {"attempted": True, "success": bool(success), "status": "sent" if success else "failed"}
    except Exception as exc:
        logger.exception("V6 unified notification failed")
        return {
            "attempted": True,
            "success": False,
            "status": "exception",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _finalize_report(
    *,
    report_dir: str,
    report_date: str,
    v4_report_path: str | None,
    v6_payload: Dict[str, Any],
    analysis_records: list[Dict[str, Any]],
) -> Dict[str, Any]:
    output = Path(report_dir)
    latest_path = output / "v6_daily_latest.md"
    dated_path = output / f"v6_daily_{report_date}.md"
    v6_markdown = latest_path.read_text(encoding="utf-8")

    v4_path = Path(v4_report_path) if v4_report_path else None
    v4_markdown = None
    if v4_path is not None and v4_path.is_file():
        v4_markdown = v4_path.read_text(encoding="utf-8")
        logger.info("[V6] 已加载同次运行的 V4 报告 Artifact: %s", v4_path)
    elif v4_report_path:
        logger.warning("[V6] 指定的 V4 报告不存在，将仅使用数据库中的 V4 结构化记录: %s", v4_report_path)

    structured_count = count_v4_structured_records(analysis_records)
    if structured_count:
        logger.info("[V6] 融合报告使用 %s 个标的的 V4 结构化投研记录", structured_count)
    else:
        logger.warning("[V6] 未发现 V4 结构化投研记录；最终报告不会回退为 V4/V6 原文拼接")

    unified = build_accuracy_unified_report(
        v6_markdown,
        v4_markdown,
        v6_payload=v6_payload,
        v4_records=analysis_records,
        report_date=report_date,
    )
    latest_path.write_text(unified, encoding="utf-8")
    dated_path.write_text(unified, encoding="utf-8")
    return {
        "language": "zh",
        "fusion_mode": "structured_v4_v6",
        "accuracy_layer": "v6.2",
        "v4_merged": structured_count > 0,
        "v4_structured_records": structured_count,
        "v4_report": str(v4_path) if v4_markdown and v4_path is not None else None,
        "output": str(latest_path),
    }


def run(
    *,
    stock_db_path: str,
    v6_db_path: str,
    report_dir: str,
    limit: int = 5000,
    min_samples: int = 50,
    primary_model: str | None = None,
    notify: bool = False,
    v4_report_path: str | None = None,
    accuracy_lab_cost_bps: float = DEFAULT_COST_BPS,
    accuracy_lab_max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
    accuracy_lab_promotion_min_samples: int = DEFAULT_PROMOTION_MIN_SAMPLES,
) -> Dict[str, Any]:
    store = V6DailyStore(v6_db_path)
    engine = V6DailyEngine()
    records = read_analysis_records(stock_db_path, limit=max(1, int(limit)))

    source_codes = list(
        dict.fromkeys(
            str(record.get("code") or "").strip().upper()
            for record in records
            if str(record.get("code") or "").strip()
        )
    )
    public_context = fetch_free_context(source_codes)

    # First build every usable signal WITHOUT newly fetched SEC/FRED. This
    # establishes the signal date independently of current external evidence.
    provisional_raw: list[tuple[Dict[str, Any], Any]] = []
    skipped_unusable = 0
    for record in records:
        history_id = int(record.get("id") or 0)
        if history_id <= 0:
            skipped_unusable += 1
            continue
        signal = engine.from_analysis_record(
            record,
            primary_model=primary_model,
            external_context=None,
        )
        if signal is None:
            skipped_unusable += 1
            continue
        provisional_raw.append((record, signal))

    provisional, duplicate_same_trade_date = _canonicalize_provisional(provisional_raw)
    effective_dates = [
        str(signal.effective_trade_date or "")
        for _, signal in provisional
        if str(signal.effective_trade_date or "")
    ]
    latest_effective_date = max(effective_dates) if effective_dates else None

    new_signals = 0
    skipped_existing = 0
    skipped_same_trade_date = duplicate_same_trade_date
    external_numeric_signals = 0

    for record, provisional_signal in provisional:
        history_id = int(record.get("id") or 0)
        if store.has_analysis_history_id(history_id):
            skipped_existing += 1
            continue

        signal = provisional_signal
        can_use_external = (
            latest_effective_date is not None
            and signal.effective_trade_date == latest_effective_date
            and _is_current_external_context_safe(
                signal.effective_trade_date,
                signal.analysis_created_at,
            )
        )
        if can_use_external:
            enriched = engine.from_analysis_record(
                record,
                primary_model=primary_model,
                external_context=public_context,
            )
            if enriched is not None:
                signal = enriched
                external_numeric_signals += 1

        if store.has_signal_key(signal.code, signal.effective_trade_date, engine.version):
            skipped_same_trade_date += 1
            continue
        if store.save_signal(signal, engine_version=engine.version):
            new_signals += 1
            horizon_text = ", ".join(
                f"{name}={item.get('direction')}/{item.get('score')}"
                for name, item in signal.horizon_forecasts.items()
            )
            logger.info(
                "[V6.2] history_id=%s code=%s type=%s decision=%s horizons=[%s] opportunity=%s risk=%s evidence=%.2f llm=%s external_numeric=%s",
                signal.analysis_history_id,
                signal.code,
                signal.instrument_type,
                signal.decision,
                horizon_text,
                signal.opportunity_score,
                signal.risk_score,
                signal.evidence_coverage,
                signal.llm_health,
                can_use_external,
            )

    if latest_effective_date and external_numeric_signals == 0:
        logger.info(
            "[V6.2] SEC/FRED 当前快照未注入历史/旧分析记录；仅作为当期报告背景展示"
        )

    maturation = mature_outcomes(store, stock_db_path)
    accuracy_lab = run_accuracy_lab(
        v6_db_path,
        stock_db_path,
        report_dir=report_dir,
        min_samples=max(3, int(min_samples)),
        promotion_min_samples=max(int(accuracy_lab_promotion_min_samples), int(min_samples)),
        cost_bps=max(0.0, float(accuracy_lab_cost_bps)),
        max_holding_bars=max(1, int(accuracy_lab_max_holding_bars)),
    )

    quick = store.quick_check()
    if quick.strip().lower() != "ok":
        raise RuntimeError(f"V6 database quick_check failed: {quick}")

    board_before_report = store.latest_board()
    codes = [
        str(item.get("code") or "").strip().upper()
        for item in board_before_report
        if str(item.get("code") or "").strip()
    ]
    lab_run = accuracy_lab.get("run") or {}
    run_stats: Dict[str, Any] = {
        "analysis_records_seen": len(records),
        "canonical_signals_seen": len(provisional),
        "new_signals": new_signals,
        "skipped_existing": skipped_existing,
        "skipped_same_trade_date": skipped_same_trade_date,
        "skipped_unusable": skipped_unusable,
        "new_outcomes": maturation["evaluated"],
        "not_yet_mature": maturation["not_yet_mature"],
        "accuracy_lab_status": accuracy_lab.get("status"),
        "new_shadow_forecasts": lab_run.get("new_shadow_forecasts", 0),
        "new_shadow_outcomes": lab_run.get("new_shadow_outcomes", 0),
        "new_trade_outcomes": lab_run.get("new_trade_outcomes", 0),
        "promotion_candidates": len(accuracy_lab.get("promotion_candidates") or []),
        "quick_check": quick,
        "free_source_enrichment": (public_context.get("status") or {}).get("enabled", False),
        "external_numeric_trade_date": latest_effective_date if external_numeric_signals else None,
        "external_numeric_signals": external_numeric_signals,
        "external_backfill_policy": "current snapshot scores only newest recently-created canonical signals",
    }
    report_date = datetime.now().strftime("%Y-%m-%d")
    payload = write_daily_report(
        store,
        report_dir,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        report_date=report_date,
        public_context=public_context,
    )
    # Keep the existing report writer stable while exposing V6.2 research data
    # to the unified report and machine-readable daily payload.
    payload["accuracy_lab"] = accuracy_lab
    (Path(report_dir) / "v6_daily_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    unified_report = _finalize_report(
        report_dir=report_dir,
        report_date=report_date,
        v4_report_path=v4_report_path,
        v6_payload=payload,
        analysis_records=records,
    )
    report_path = Path(report_dir) / "v6_daily_latest.md"
    notification = _notify(report_path, codes) if notify else {
        "attempted": False,
        "success": False,
        "status": "disabled",
    }

    result = {
        "version": engine.version,
        "accuracy_layer": "v6.2",
        "database": str(store.path),
        "report": str(report_path),
        "unified_report": unified_report,
        "run": run_stats,
        "scoreboard_status": (payload.get("scoreboard") or {}).get("status"),
        "accuracy_lab": {
            "version": accuracy_lab.get("version"),
            "status": accuracy_lab.get("status"),
            "promotion_candidates": accuracy_lab.get("promotion_candidates") or [],
            "strategy_status": (accuracy_lab.get("strategy") or {}).get("status"),
            "artifacts": accuracy_lab.get("artifacts") or {},
        },
        "free_sources": public_context.get("status") or {},
        "notification": notification,
    }
    (Path(report_dir) / "v6_run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the V6.2 deterministic multi-horizon US-stock daily intelligence and accuracy lab")
    parser.add_argument("--stock-db", default=os.getenv("STOCK_DB_PATH", "data/stock_analysis.db"))
    parser.add_argument("--v6-db", default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"))
    parser.add_argument("--report-dir", default=os.getenv("V6_DAILY_REPORT_DIR", "v6_reports"))
    parser.add_argument("--v4-report", default=os.getenv("V6_UPSTREAM_V4_REPORT"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("V6_DAILY_SCAN_LIMIT", "5000")))
    parser.add_argument("--min-samples", type=int, default=int(os.getenv("V6_DAILY_MIN_SAMPLES", "50")))
    parser.add_argument("--primary-model", default=os.getenv("LITELLM_MODEL") or os.getenv("V6_PRIMARY_LLM_MODEL"))
    parser.add_argument("--notify", action="store_true", default=_truthy(os.getenv("V6_DAILY_NOTIFY", "false")))
    parser.add_argument(
        "--accuracy-lab-cost-bps",
        type=float,
        default=float(os.getenv("V6_ACCURACY_LAB_COST_BPS", str(DEFAULT_COST_BPS))),
    )
    parser.add_argument(
        "--accuracy-lab-max-holding-bars",
        type=int,
        default=int(os.getenv("V6_ACCURACY_LAB_MAX_HOLDING_BARS", str(DEFAULT_MAX_HOLDING_BARS))),
    )
    parser.add_argument(
        "--accuracy-lab-promotion-min-samples",
        type=int,
        default=int(os.getenv("V6_ACCURACY_LAB_PROMOTION_MIN_SAMPLES", str(DEFAULT_PROMOTION_MIN_SAMPLES))),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    result = run(
        stock_db_path=args.stock_db,
        v6_db_path=args.v6_db,
        report_dir=args.report_dir,
        limit=args.limit,
        min_samples=args.min_samples,
        primary_model=args.primary_model,
        notify=args.notify,
        v4_report_path=args.v4_report,
        accuracy_lab_cost_bps=args.accuracy_lab_cost_bps,
        accuracy_lab_max_holding_bars=args.accuracy_lab_max_holding_bars,
        accuracy_lab_promotion_min_samples=args.accuracy_lab_promotion_min_samples,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
