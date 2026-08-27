from __future__ import annotations

import json
from pathlib import Path

from scripts.run_us_open_confirmation_safe import (
    _append_v73_reliability,
    _reliability_by_symbol,
)


def _payload() -> dict:
    return {
        "board": [
            {
                "code": "MSFT",
                "context_features": {
                    "forecast_intelligence": {
                        "horizons": {
                            "1d": {
                                "probability_up": 0.60,
                                "calibration_status": "mature",
                                "calibration_samples": 52,
                            },
                            "5d": {
                                "probability_up": 0.55,
                                "calibration_status": "mature",
                                "calibration_samples": 50,
                                "historical_direction_hit_rate": 0.52,
                                "evidence_confidence": 0.78,
                            },
                            "10d": {
                                "probability_up": 0.51,
                                "calibration_status": "shrunk",
                                "calibration_samples": 16,
                                "diagnostics": {"decision_weight": 0.0},
                            },
                            "20d": {
                                "probability_up": 0.77,
                                "calibration_status": "prior_only",
                                "calibration_samples": 0,
                            },
                        }
                    }
                },
            }
        ]
    }


def test_reliability_reader_uses_close_forecast_metadata(tmp_path: Path) -> None:
    path = tmp_path / "close.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    reliability = _reliability_by_symbol(path)
    assert set(reliability) == {"MSFT"}
    assert reliability["MSFT"]["20d"]["calibration_status"] == "prior_only"
    assert reliability["MSFT"]["10d"]["diagnostics"]["decision_weight"] == 0.0


def test_open_report_distinguishes_tendency_hit_rate_and_evidence_score(
    tmp_path: Path,
) -> None:
    path = tmp_path / "close.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    reliability = _reliability_by_symbol(path)
    source = (
        "**预测可信度**：78%\n"
        "| 标的 | 1D上涨概率 | 5D上涨概率 | 更好买点评分 |\n"
        "|---|---:|---:|---:|\n"
    )
    report = _append_v73_reliability(source, reliability)

    assert "**模型证据置信分**" in report
    assert "| 标的 | 1D预测 | 5D预测 | 更好买点评分 |" in report
    assert "## V7.3 预测可靠度" in report
    assert "倾向 77%（未校准 n=0）" in report
    assert "概率 55%（mature, n=50）" in report
    assert "| MSFT |" in report
    assert "| 0% |" in report
    assert "52.0%" in report
    assert "78%" in report
    assert "模型证据置信分` 不是胜率" in report
    assert "10D 在少于 50 个成熟样本时交易权重固定为 0%" in report
