from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from src.alpha_engine.shadow_store import read_analysis_records

from .accuracy_lab import (
    DEFAULT_COST_BPS,
    DEFAULT_MAX_HOLDING_BARS,
    DEFAULT_PROMOTION_MIN_SAMPLES,
)
from .accuracy_report import build_accuracy_unified_report
from .decision_audit import FinalDecisionAuditStore
from .engine import V6DailyEngine
from .final_decision_renderer import (
    apply_final_decision_contract,
    assert_final_decision_report_consistency,
)
from .final_decision_service import (
    build_final_decision_packets,
    build_final_decision_payload,
)
from .free_sources import fetch_free_context
from .legacy_retirement import assert_legacy_retirement_ready
from .legacy_write_guard import assert_legacy_facts_unchanged, snapshot_legacy_facts
from .normalized_accuracy_lab import run_normalized_accuracy_lab
from .normalized_manifest_store import NormalizedV6ManifestStore
from .production_cutover import cutover_daily_payload
from .production_import_guard import assert_production_import_graph_clean
from .production_outcomes import mature_normalized_outcomes
from .production_read_store import ProductionV6ReadStore
from .production_report import render_daily_markdown, write_daily_report
from .production_write_store import ProductionV6WriteStore
from .unified_report import count_v4_structured_records


logger = logging.getLogger("v6_daily")
STAGE11_ENTRYPOINT_VERSION = "v6-stage11-legacy-schema-archival-v1"
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
        return {
            "attempted": True,
            "success": bool(success),
            "status": "sent" if success else "failed",
        }
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
        logger.warning(
            "[V6] 指定的 V4 报告不存在，将仅使用数据库中的 V4 结构化记录: %s",
            v4_report_path,
        )

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
    final_packets = build_final_decision_packets(v6_payload, v4_records=analysis_records)
    unified = apply_final_decision_contract(unified, final_packets)
    assert_final_decision_report_consistency(unified, final_packets)
    latest_path.write_text(unified, encoding="utf-8")
    dated_path.write_text(unified, encoding="utf-8")
    return {
        "language": "zh",
        "fusion_mode": "structured_v4_v6",
        "decision_source": "FinalDecisionPacket",
        "decision_contract": "final-decision-packet-v1",
        "accuracy_layer": "v6.2",
        "v4_merged": structured_count > 0,
        "v4_structured_records": structured_count,
        "v4_report": str(v4_path) if v4_markdown and v4_path is not None else None,
        "final_decision_packets": len(final_packets),
        "output": str(latest_path),
    }


def _persist_metadata(
    result: Dict[str, Any],
    *,
    report_dir: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    result.update(metadata)
    output = Path(report_dir)
    run_path = output / "v6_run.json"
    payload_path = output / "v6_daily_latest.json"
    for path in (run_path, payload_path):
        if not path.is_file():
            raise RuntimeError(f"Stage 11 expected output missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(metadata)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


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
    repo_root: str | Path | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    import_guard = assert_production_import_graph_clean(root)
    legacy_before = snapshot_legacy_facts(v6_db_path)

    engine = V6DailyEngine(history_db_path=v6_db_path)
    store = ProductionV6WriteStore(
        v6_db_path,
        active_engine_version=engine.version,
    )
    schema_registry = dict(store.schema_registry)
    records = read_analysis_records(stock_db_path, limit=max(1, int(limit)))

    source_codes = list(
        dict.fromkeys(
            str(record.get("code") or "").strip().upper()
            for record in records
            if str(record.get("code") or "").strip()
        )
    )
    public_context = fetch_free_context(source_codes)

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
        if store.has_analysis_history_version(history_id):
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

    maturation = mature_normalized_outcomes(store, stock_db_path)
    accuracy_lab = run_normalized_accuracy_lab(
        v6_db_path,
        stock_db_path,
        report_dir=report_dir,
        active_engine_version=engine.version,
        min_samples=max(3, int(min_samples)),
        promotion_min_samples=max(int(accuracy_lab_promotion_min_samples), int(min_samples)),
        cost_bps=max(0.0, float(accuracy_lab_cost_bps)),
        max_holding_bars=max(1, int(accuracy_lab_max_holding_bars)),
    )

    quick = store.quick_check()
    if quick.strip().lower() != "ok":
        raise RuntimeError(f"V6 database quick_check failed: {quick}")

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
        "signal_identity_migrated": False,
        "canonical_write_bootstrap": False,
        "write_source": "normalized_v6_tables",
        "active_engine_version": engine.version,
        "free_source_enrichment": (public_context.get("status") or {}).get("enabled", False),
        "external_numeric_trade_date": latest_effective_date if external_numeric_signals else None,
        "external_numeric_signals": external_numeric_signals,
        "external_backfill_policy": "current snapshot scores only newest recently-created canonical signals",
        "production_runner": "native_normalized_stage11",
    }
    report_date = datetime.now().strftime("%Y-%m-%d")

    read_store = ProductionV6ReadStore(
        v6_db_path,
        active_engine_version=engine.version,
    )
    reference_payload = write_daily_report(
        read_store,
        report_dir,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        report_date=report_date,
        public_context=public_context,
    )

    normalized_storage = NormalizedV6ManifestStore(v6_db_path).persist_snapshot(
        reference_payload,
        source_engine_version=engine.version,
        report_date=report_date,
        run_mode="LIVE",
        metadata={
            "accuracy_layer": "v6.2",
            "migration_phase": "legacy_schema_archival",
            "canonical_write_source": "normalized_v6_tables",
            "legacy_role": "historical_read_only_explicit_migration_source",
            "production_runner": "native_normalized_stage11",
        },
    )
    write_path = store.write_status()
    if write_path.get("parity") != "exact":
        raise RuntimeError(f"V6 canonical write parity failed: {write_path}")
    run_stats["write_parity"] = write_path.get("parity")

    requested_read_source = os.getenv("V6_DAILY_READ_SOURCE", "normalized")
    payload, read_cutover = cutover_daily_payload(
        reference_payload,
        db_path=v6_db_path,
        active_engine_version=engine.version,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        public_context=public_context,
        requested_source=requested_read_source,
    )
    run_stats["read_source"] = read_cutover.get("selected_source")
    run_stats["read_parity"] = read_cutover.get("parity")
    payload["run"] = run_stats
    payload["accuracy_lab"] = accuracy_lab
    payload["write_path"] = write_path
    payload["normalized_storage"] = normalized_storage

    final_packets = build_final_decision_packets(payload, v4_records=records)
    payload["final_decisions"] = build_final_decision_payload(payload, v4_records=records)

    decision_audit = FinalDecisionAuditStore(v6_db_path).persist_packets(
        payload,
        final_packets,
        report_date=report_date,
        source_engine_version=engine.version,
    )
    payload["decision_audit"] = decision_audit

    output = Path(report_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "v6_daily_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    normalized_markdown = render_daily_markdown(payload, report_date=report_date)
    (output / "v6_daily_latest.md").write_text(normalized_markdown, encoding="utf-8")
    (output / f"v6_daily_{report_date}.md").write_text(normalized_markdown, encoding="utf-8")

    codes = [
        str(item.get("code") or "").strip().upper()
        for item in (payload.get("board") or [])
        if isinstance(item, dict) and str(item.get("code") or "").strip()
    ]
    unified_report = _finalize_report(
        report_dir=report_dir,
        report_date=report_date,
        v4_report_path=v4_report_path,
        v6_payload=payload,
        analysis_records=records,
    )
    report_path = output / "v6_daily_latest.md"
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
        "final_decisions": (payload.get("final_decisions") or {}).get("summary") or {},
        "decision_audit": decision_audit,
        "write_path": write_path,
        "normalized_storage": normalized_storage,
        "read_cutover": read_cutover,
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
    (output / "v6_run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    legacy_after = snapshot_legacy_facts(v6_db_path)
    write_guard = assert_legacy_facts_unchanged(legacy_before, legacy_after)
    retirement = assert_legacy_retirement_ready(
        v6_db_path,
        projection_enabled=False,
    )
    retirement["entrypoint_version"] = STAGE11_ENTRYPOINT_VERSION
    archival = {
        "schema_version": "v6-legacy-archival-policy-v1",
        "migration_policy": "explicit_cli_only",
        "archive_policy": "explicit_cli_only",
        "automatic_migration": False,
        "automatic_archive": False,
        "drop_legacy_tables": False,
        "archive_cli": "scripts/archive_v6_legacy.py",
        "migration_cli": "scripts/migrate_v6_legacy.py",
    }
    metadata = {
        "stage11_entrypoint": STAGE11_ENTRYPOINT_VERSION,
        "schema_registry": schema_registry,
        "production_import_guard": import_guard,
        "legacy_retirement": retirement,
        "legacy_write_guard": write_guard,
        "legacy_archival": archival,
    }
    return _persist_metadata(result, report_dir=report_dir, metadata=metadata)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Stage 11 normalized-only V6 daily intelligence pipeline"
    )
    parser.add_argument("--stock-db", default=os.getenv("STOCK_DB_PATH", "data/stock_analysis.db"))
    parser.add_argument("--v6-db", default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"))
    parser.add_argument("--report-dir", default=os.getenv("V6_DAILY_REPORT_DIR", "v6_reports"))
    parser.add_argument("--v4-report", default=os.getenv("V6_UPSTREAM_V4_REPORT"))
    parser.add_argument("--limit", type=int, default=int(os.getenv("V6_DAILY_SCAN_LIMIT", "5000")))
    parser.add_argument(
        "--min-samples",
        type=int,
        default=int(os.getenv("V6_DAILY_MIN_SAMPLES", "50")),
    )
    parser.add_argument(
        "--primary-model",
        default=os.getenv("LITELLM_MODEL") or os.getenv("V6_PRIMARY_LLM_MODEL"),
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        default=_truthy(os.getenv("V6_DAILY_NOTIFY", "false")),
    )
    parser.add_argument(
        "--accuracy-lab-cost-bps",
        type=float,
        default=float(os.getenv("V6_ACCURACY_LAB_COST_BPS", str(DEFAULT_COST_BPS))),
    )
    parser.add_argument(
        "--accuracy-lab-max-holding-bars",
        type=int,
        default=int(
            os.getenv("V6_ACCURACY_LAB_MAX_HOLDING_BARS", str(DEFAULT_MAX_HOLDING_BARS))
        ),
    )
    parser.add_argument(
        "--accuracy-lab-promotion-min-samples",
        type=int,
        default=int(
            os.getenv(
                "V6_ACCURACY_LAB_PROMOTION_MIN_SAMPLES",
                str(DEFAULT_PROMOTION_MIN_SAMPLES),
            )
        ),
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
