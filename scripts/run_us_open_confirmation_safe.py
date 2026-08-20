from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_us_open_confirmation import (
    load_final_packets,
    notify_report,
    write_outputs,
)
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2, run as run_v2


logger = logging.getLogger("us_open_confirmation_safe")
NY = ZoneInfo("America/New_York")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; use default %s", name, raw, default)
        return default


def _policy() -> dict[str, float]:
    return {
        "normal_chase_tolerance_pct": _env_float("OPEN_CONFIRMATION_CHASE_TOLERANCE_PCT", 0.5),
        "momentum_chase_tolerance_pct": _env_float("OPEN_CONFIRMATION_MOMENTUM_CHASE_PCT", 0.75),
        "weak_open_pct": _env_float("OPEN_CONFIRMATION_WEAK_OPEN_PCT", -0.5),
        "min_volume_ratio": _env_float("OPEN_CONFIRMATION_MIN_VOLUME_RATIO", 0.70),
        "min_opening_range_position": _env_float("OPEN_CONFIRMATION_MIN_OPENING_RANGE_POSITION", 0.25),
        "momentum_min_opening_range_position": _env_float("OPEN_CONFIRMATION_MOMENTUM_MIN_RANGE_POSITION", 0.50),
        "momentum_min_volume_ratio": _env_float("OPEN_CONFIRMATION_MOMENTUM_MIN_VOLUME_RATIO", 0.70),
        "max_quote_age_minutes": _env_float("OPEN_CONFIRMATION_MAX_QUOTE_AGE_MINUTES", 8.0),
        "starter_position_pct": _env_float("OPEN_CONFIRMATION_STARTER_POSITION_PCT", 10.0),
    }


def _near_open_retry_seconds(now: datetime | None = None) -> float:
    current = (now or datetime.now(NY)).astimezone(NY)
    if current.hour != 9 or current.minute >= 35:
        return 0.0
    if current.minute < 30:
        return 0.0
    target = current.replace(hour=9, minute=35, second=5, microsecond=0)
    return max(0.0, min(300.0, (target - current).total_seconds()))


def _fallback_data_unavailable(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
    reason: str,
) -> dict[str, Any]:
    generated_at = datetime.now(NY)
    params = _policy()
    decisions = []

    for packet in load_final_packets(v6_payload_path):
        identity = _mapping(packet.get("identity"))
        symbol = str(identity.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        decisions.append(
            classify_confirmation_v2(
                packet,
                None,
                evaluated_at=generated_at,
                data_error=(
                    "当前开盘实时行情暂时不可验证。系统不会在缺少可靠行情时给出买入授权；"
                    f"当前不要下单。技术信息：{reason}"
                ),
                **params,
            )
        )

    if not decisions:
        raise RuntimeError("no symbols available in prior final decision payload")

    report_path, json_path = write_outputs(
        decisions,
        output_dir=output_dir,
        generated_at=generated_at,
        source_run_id=source_run_id,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["policy_version"] = "us-open-confirmation-v2-runtime-safe-fallback"
    payload["fallback"] = {
        "active": True,
        "reason": reason,
        "instruction": "行情不可验证时禁止新开仓；本邮件用于避免自动化静默失败。",
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    sent = notify_report(
        report_path,
        decisions,
        generated_at.strftime("%Y-%m-%d"),
    ) if notify else False
    if notify and not sent:
        raise RuntimeError("fallback open confirmation notification failed")

    return {
        "report": str(report_path),
        "json": str(json_path),
        "symbols": len(decisions),
        "live_success": 0,
        "buy_now": 0,
        "waiting": 0,
        "no_buy": 0,
        "data_unavailable": len(decisions),
        "notified": sent,
        "fallback": True,
    }


def _run_v2_once(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
    params: Mapping[str, float],
) -> dict[str, Any]:
    return run_v2(
        v6_payload_path=v6_payload_path,
        output_dir=output_dir,
        notify=notify,
        source_run_id=source_run_id,
        **params,
    )


def run_safe(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
) -> dict[str, Any]:
    params = _policy()
    try:
        return _run_v2_once(
            v6_payload_path=v6_payload_path,
            output_dir=output_dir,
            notify=notify,
            source_run_id=source_run_id,
            params=params,
        )
    except RuntimeError as exc:
        text = str(exc)
        if "all live U.S. session quotes unavailable" not in text:
            raise

        retry_seconds = _near_open_retry_seconds()
        if retry_seconds > 0:
            logger.info(
                "opening bars are still warming up; retry live confirmation in %.1f seconds",
                retry_seconds,
            )
            time_module.sleep(retry_seconds)
            try:
                return _run_v2_once(
                    v6_payload_path=v6_payload_path,
                    output_dir=output_dir,
                    notify=notify,
                    source_run_id=source_run_id,
                    params=params,
                )
            except RuntimeError as retry_exc:
                text = str(retry_exc)
                if "all live U.S. session quotes unavailable" not in text:
                    raise

        logger.warning("all live quotes unavailable; emit fail-safe confirmation email")
        return _fallback_data_unavailable(
            v6_payload_path=v6_payload_path,
            output_dir=output_dir,
            source_run_id=source_run_id,
            notify=notify,
            reason=text,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-safe U.S. open runtime confirmation that uses the actual execution clock"
    )
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument("--output-dir", default="open_confirmation_reports")
    parser.add_argument("--source-run-id", default=os.getenv("OPEN_CONFIRMATION_SOURCE_RUN_ID"))
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    result = run_safe(
        v6_payload_path=args.v6_payload,
        output_dir=args.output_dir,
        source_run_id=args.source_run_id,
        notify=args.notify,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
