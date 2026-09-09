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

from scripts import run_us_open_timing as timing_module
import scripts.us_open_terminal_lock as terminal_module

logger = logging.getLogger("us_open_confirmation_safe")
NY = ZoneInfo("America/New_York")


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; use default %s", name, raw, default)
        return default


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _policy() -> dict[str, float]:
    return {
        "normal_chase_tolerance_pct": _env_float(
            "OPEN_CONFIRMATION_CHASE_TOLERANCE_PCT", 0.5
        ),
        "momentum_chase_tolerance_pct": _env_float(
            "OPEN_CONFIRMATION_MOMENTUM_CHASE_PCT", 0.75
        ),
        "weak_open_pct": _env_float("OPEN_CONFIRMATION_WEAK_OPEN_PCT", -0.5),
        "min_volume_ratio": _env_float("OPEN_CONFIRMATION_MIN_VOLUME_RATIO", 0.70),
        "min_opening_range_position": _env_float(
            "OPEN_CONFIRMATION_MIN_OPENING_RANGE_POSITION", 0.25
        ),
        "momentum_min_opening_range_position": _env_float(
            "OPEN_CONFIRMATION_MOMENTUM_MIN_RANGE_POSITION", 0.50
        ),
        "momentum_min_volume_ratio": _env_float(
            "OPEN_CONFIRMATION_MOMENTUM_MIN_VOLUME_RATIO", 0.70
        ),
        "max_quote_age_minutes": _env_float(
            "OPEN_CONFIRMATION_MAX_QUOTE_AGE_MINUTES", 8.0
        ),
        "starter_position_pct": _env_float(
            "OPEN_CONFIRMATION_STARTER_POSITION_PCT", 10.0
        ),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reliability_by_symbol(v6_payload_path: str | Path) -> dict[str, dict[str, Any]]:
    """Read V7.3 forecast reliability metadata from the prior close payload."""
    try:
        payload = json.loads(Path(v6_payload_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw in payload.get("board") or []:
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("code") or "").strip().upper()
        if not symbol:
            continue
        intelligence = _mapping(
            _mapping(raw.get("context_features")).get("forecast_intelligence")
        )
        horizons = _mapping(intelligence.get("horizons"))
        if horizons:
            result[symbol] = dict(horizons)
    return result


def _probability_text(block: Mapping[str, Any]) -> str:
    try:
        probability = float(block.get("probability_up"))
    except (TypeError, ValueError):
        return "N/A"
    status = str(block.get("calibration_status") or "prior_only")
    try:
        samples = int(block.get("calibration_samples") or 0)
    except (TypeError, ValueError):
        samples = 0
    if status == "prior_only" or samples <= 0:
        return f"倾向 {probability:.0%}（未校准 n={samples}）"
    return f"概率 {probability:.0%}（{status}, n={samples}）"


def _hit_rate_text(block: Mapping[str, Any]) -> str:
    value = block.get("historical_direction_hit_rate")
    if value is None:
        value = _mapping(block.get("diagnostics")).get("historical_direction_hit_rate")
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "N/A"


def _baseline_text(block: Mapping[str, Any]) -> str:
    value = block.get("historical_majority_baseline_accuracy")
    if value is None:
        value = _mapping(block.get("diagnostics")).get(
            "historical_majority_baseline_accuracy"
        )
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "N/A"


def _skill_text(block: Mapping[str, Any]) -> str:
    hit = block.get("historical_direction_hit_rate")
    baseline = block.get("historical_majority_baseline_accuracy")
    diagnostics = _mapping(block.get("diagnostics"))
    if hit is None:
        hit = diagnostics.get("historical_direction_hit_rate")
    if baseline is None:
        baseline = diagnostics.get("historical_majority_baseline_accuracy")
    try:
        return f"{float(hit) - float(baseline):+.1%}"
    except (TypeError, ValueError):
        return "N/A"


def _scope_text(block: Mapping[str, Any]) -> str:
    value = _mapping(block.get("diagnostics")).get("calibration_scope")
    if value is None:
        value = block.get("calibration_scope")
    return str(value or "N/A")


def _evidence_text(block: Mapping[str, Any]) -> str:
    value = block.get("evidence_confidence")
    if value is None:
        value = block.get("forecast_confidence")
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "N/A"


def _decision_weight_text(block: Mapping[str, Any]) -> str:
    value = _mapping(block.get("diagnostics")).get("decision_weight")
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "0%"


def _append_v73_reliability(
    report: str,
    reliability: Mapping[str, Mapping[str, Any]],
) -> str:
    text = str(report or "")
    text = text.replace("**预测可信度**", "**模型证据分**")
    text = text.replace("**模型证据置信分**", "**模型证据分**")
    text = text.replace(
        "| 1D上涨概率 | 5D上涨概率 |",
        "| 1D研究倾向 | 5D研究倾向 |",
    )
    text = text.replace(
        "| 1D预测 | 5D预测 |",
        "| 1D研究倾向 | 5D研究倾向 |",
    )
    if not reliability or "## V7.3 预测可靠度" in text:
        return text

    lines = [
        "",
        "## V7.5 方向可靠度",
        "",
        "> `模型证据分` 不是胜率。方向命中率已按“预测方向是否等于实际涨跌”计算；只有命中率≥52%、成熟样本≥50，且至少高于多数类基线 2 个百分点时，方向模型才允许进入生产动作。",
        "",
        "| 标的 | 1D研究倾向 | 5D研究倾向 | 5D方向命中 | 多数基线 | 方向Skill | 校准范围 | 10D生产权重 | 20D研究倾向 | 5D模型证据分 |",
        "|---|---|---|---:|---:|---:|---|---:|---|---:|",
    ]
    for symbol, horizons in reliability.items():
        h1 = _mapping(horizons.get("1d"))
        h5 = _mapping(horizons.get("5d"))
        h10 = _mapping(horizons.get("10d"))
        h20 = _mapping(horizons.get("20d"))
        lines.append(
            f"| {symbol} | {_probability_text(h1)} | {_probability_text(h5)} | "
            f"{_hit_rate_text(h5)} | {_baseline_text(h5)} | {_skill_text(h5)} | "
            f"{_scope_text(h5)} | {_decision_weight_text(h10)} | "
            f"{_probability_text(h20)} | {_evidence_text(h5)} |"
        )
    lines += [
        "",
        "- 方向 Skill = 历史方向命中率 − 多数类基线准确率；Skill < +2 个百分点时生产权重固定为 0。",
        "- 10D 与其他 horizon 使用同一方向 Skill 门；未达标时仅保留研究观察。",
        "- 风险层 `REJECTED/NO_BUY` 是执行许可，不等于实时看跌；方向信号与执行动作在邮件中分开展示。",
    ]
    return text.rstrip() + "\n" + "\n".join(lines) + "\n"


def _run_with_report_semantics(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
    previous_state_path: str | Path | None,
    force_notify: bool,
    allow_all_unavailable: bool,
) -> dict[str, Any]:
    reliability = _reliability_by_symbol(v6_payload_path)
    original_timing_render = timing_module.render_markdown
    original_terminal_render = terminal_module.render_markdown

    def render_v73(decisions, *, generated_at, source_run_id):
        base = original_timing_render(
            decisions,
            generated_at=generated_at,
            source_run_id=source_run_id,
        )
        return _append_v73_reliability(base, reliability)

    # Both the no-lock and terminal-lock paths render through one of these module
    # globals. Patch only for this synchronous run and restore in finally.
    timing_module.render_markdown = render_v73
    terminal_module.render_markdown = render_v73
    try:
        return terminal_module.run_with_terminal_lock(
            v6_payload_path=v6_payload_path,
            output_dir=output_dir,
            source_run_id=source_run_id,
            notify=notify,
            previous_state_path=previous_state_path,
            force_notify=force_notify,
            allow_all_unavailable=allow_all_unavailable,
            **_policy(),
        )
    finally:
        timing_module.render_markdown = original_timing_render
        terminal_module.render_markdown = original_terminal_render


def _near_open_retry_seconds(now: datetime | None = None) -> float:
    # Do not block a latency-sensitive runner for several minutes. The workflow
    # itself has dense 09:30-09:35 ET retry candidates, so a missing first bar
    # should finish quickly and let the next candidate re-evaluate.
    del now
    return 0.0


def _run_once(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
    previous_state_path: str | Path | None,
    force_notify: bool,
    allow_all_unavailable: bool,
) -> dict[str, Any]:
    return _run_with_report_semantics(
        v6_payload_path=v6_payload_path,
        output_dir=output_dir,
        source_run_id=source_run_id,
        notify=notify,
        previous_state_path=previous_state_path,
        force_notify=force_notify,
        allow_all_unavailable=allow_all_unavailable,
    )


def run_safe(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
    previous_state_path: str | Path | None = None,
    force_notify: bool = False,
) -> dict[str, Any]:
    try:
        return _run_once(
            v6_payload_path=v6_payload_path,
            output_dir=output_dir,
            source_run_id=source_run_id,
            notify=notify,
            previous_state_path=previous_state_path,
            force_notify=force_notify,
            allow_all_unavailable=False,
        )
    except RuntimeError as exc:
        text = str(exc)
        if "all live U.S. session quotes unavailable" not in text:
            raise
        retry_seconds = _near_open_retry_seconds()
        if retry_seconds > 0:
            logger.info(
                "opening bars are warming up; retry V7 intraday timing in %.1f seconds",
                retry_seconds,
            )
            time_module.sleep(retry_seconds)
            try:
                return _run_once(
                    v6_payload_path=v6_payload_path,
                    output_dir=output_dir,
                    source_run_id=source_run_id,
                    notify=notify,
                    previous_state_path=previous_state_path,
                    force_notify=force_notify,
                    allow_all_unavailable=False,
                )
            except RuntimeError as retry_exc:
                if "all live U.S. session quotes unavailable" not in str(retry_exc):
                    raise
        logger.warning("all live quotes unavailable; emit non-actionable V7 timing state")
        return _run_once(
            v6_payload_path=v6_payload_path,
            output_dir=output_dir,
            source_run_id=source_run_id,
            notify=notify,
            previous_state_path=previous_state_path,
            force_notify=force_notify,
            allow_all_unavailable=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-safe V7 U.S. open timing decision using the actual execution clock"
    )
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument("--output-dir", default="open_confirmation_reports")
    parser.add_argument(
        "--source-run-id", default=os.getenv("OPEN_CONFIRMATION_SOURCE_RUN_ID")
    )
    parser.add_argument(
        "--previous-state", default=os.getenv("OPEN_CONFIRMATION_PREVIOUS_STATE")
    )
    parser.add_argument("--notify", action="store_true")
    parser.add_argument(
        "--force-notify",
        action="store_true",
        default=_truthy(os.getenv("OPEN_CONFIRMATION_FORCE_NOTIFY")),
    )
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
        previous_state_path=args.previous_state,
        force_notify=args.force_notify,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
