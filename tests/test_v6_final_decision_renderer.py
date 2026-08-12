from __future__ import annotations

import json

import pytest

from src.v6_daily.accuracy_report import (
    _standardize_stock_cards,
    build_investor_email_markdown,
)
from src.v6_daily.final_decision_renderer import (
    apply_final_decision_contract,
    assert_final_decision_report_consistency,
)
from src.v6_daily.final_decision_service import (
    FINAL_DECISION_PAYLOAD_VERSION,
    build_final_decision_packets,
    build_final_decision_payload,
)
from src.v6_daily.unified_report import render_integrated_chinese_report


def _payload() -> dict:
    return {
        "version": "v6-cutover-test",
        "generated_at": "2026-08-12T03:00:00Z",
        "market_pulse": {
            "regime": "risk_on",
            "breadth": "neutral",
            "average_opportunity": 77.7,
            "average_risk": 47.1,
            "average_evidence_coverage": 0.77,
        },
        "board": [
            {
                "code": "MSFT",
                "instrument_type": "STOCK",
                "effective_trade_date": "2026-08-11",
                "decision": "WATCH",
                "direction": "bullish",
                "forecast_score": 83.5,
                "opportunity_score": 77.7,
                "quality_score": 76.8,
                "risk_score": 47.1,
                "evidence_coverage": 0.77,
                "llm_health": "healthy",
                "features": {},
                "trade_plan": {
                    "action": "WATCH",
                    "entry_zone": [502.0, 506.0],
                    "stop_loss": 487.0,
                    "targets": [544.0, 563.0],
                    "risk_reward": 2.0,
                    "max_position_pct": 0.05,
                },
                "catalysts": ["量化层趋势与相对强弱仍偏多"],
                "risks": ["量化层风险分仍需约束仓位"],
                "limitations": [
                    "source-backed deterministic catalyst unavailable",
                    "盘中技术覆盖不可用",
                    "quote: fallback",
                ],
            }
        ],
        "deltas": [],
        "public_context": {},
        "scoreboard": {
            "status": "insufficient_data",
            "minimum_samples": 50,
            "horizons": [],
        },
        "run": {"new_signals": 1, "quick_check": "ok"},
    }


def _record() -> dict:
    raw = {
        "name": "Microsoft Corporation",
        "analysis_summary": "微软中期逻辑偏多，但短期超买，需要等待确认。",
        "risk_warning": "RSI超买且消息面存在风险。",
        "forecast": {
            "primary_horizon": "10d",
            "horizons": {
                "10d": {
                    "direction": "bullish",
                    "expected_return_pct": 3.0,
                    "rationale": "中期趋势仍向上，但短线需要消化超买。",
                }
            },
        },
        "execution": {"operation_advice": "观望", "action": "watch"},
        "dashboard": {
            "core_conclusion": {"one_sentence": "趋势偏多但不追价。"},
            "intelligence": {
                "positive_catalysts": ["AI与云业务增长继续提供支撑"],
                "risk_alerts": ["短线超买可能引发回调"],
            },
            "battle_plan": {},
            "phase_decision": {
                "watch_conditions": ["回踩后能否获得支撑", "上涨时量能是否放大"],
                "phase_context": {"phase": "postmarket", "is_trading_day": True},
            },
            "signal_attribution": {
                "strongest_bullish_signal": "多头排列且相对强弱领先",
                "strongest_bearish_signal": "RSI超买且上涨缩量",
            },
            "data_perspective": {},
        },
    }
    return {
        "id": 100,
        "code": "MSFT",
        "raw_result": json.dumps(raw, ensure_ascii=False),
    }


def _googl_wait_case() -> tuple[dict, dict]:
    payload = _payload()
    item = payload["board"][0]
    item.update(
        {
            "code": "GOOGL",
            "decision": "WAIT",
            "direction": "neutral",
            "forecast_score": 40.3,
            "opportunity_score": 47.2,
            "quality_score": 61.2,
            "risk_score": 45.7,
            "trade_plan": {
                "action": "WAIT",
                "entry_zone": [],
                "stop_loss": None,
                "targets": [],
                "risk_reward": None,
                "max_position_pct": 0.0,
            },
        }
    )
    record = _record()
    record["code"] = "GOOGL"
    raw = json.loads(record["raw_result"])
    raw["name"] = "Alphabet Inc."
    raw["forecast"]["horizons"]["10d"].update(
        {
            "direction": "bearish",
            "expected_return_pct": -3.0,
            "rationale": "短期技术结构仍偏弱。",
        }
    )
    raw["execution"] = {"operation_advice": "减仓", "action": "reduce"}
    record["raw_result"] = json.dumps(raw, ensure_ascii=False)
    return payload, record


def test_machine_readable_payload_contains_final_decision_contract() -> None:
    final_payload = build_final_decision_payload(_payload(), v4_records=[_record()])

    assert final_payload["version"] == FINAL_DECISION_PAYLOAD_VERSION
    assert final_payload["summary"]["symbols"] == 1
    assert final_payload["summary"]["fusion_complete"] == 1
    assert final_payload["summary"]["worth_buying"] == 1
    assert final_payload["summary"]["execution_authorized"] == 0
    packet = final_payload["packets"][0]
    assert packet["assessment"]["scope"] == "v4_v6_final_fusion"
    assert packet["assessment"]["is_final"] is True
    assert packet["assessment"]["verdict"] == "conditional_buy"
    assert packet["assessment"]["worth_buying"] is True
    assert packet["assessment"]["execution_authorized"] is False
    assert packet["execution"]["entry_zone"] == [502.0, 506.0]
    assert packet["execution"]["max_position_pct"] == 0.05


def test_final_contract_normalizes_unauthorized_conditional_plan_for_email() -> None:
    payload = _payload()
    records = [_record()]
    raw_report = render_integrated_chinese_report(
        payload,
        v4_records=records,
        report_date="2026-08-12",
    )
    raw_report = raw_report.replace(
        "- **数据限制**：",
        "- **舆情/新闻**：市场舆情平静，暂无重大催化或利空。\n- **数据限制**：",
        1,
    )
    packets = build_final_decision_packets(payload, v4_records=records)

    report = apply_final_decision_contract(raw_report, packets)
    assert_final_decision_report_consistency(report, packets)

    assert "# AI 美股综合日报 · 2026-08-11" in report
    assert "**当前执行授权**：**否**" in report
    assert "**当前可执行仓位上限**：**0.0%**" in report
    assert "**条件触发后最大仓位上限**：**5.0%**" in report
    assert "**V6 条件触发后最大仓位上限**" in report
    assert "当前执行 **观望**" not in report
    assert "上游投研动作 **观望**（非最终执行）" in report
    assert "市场舆情平静，暂无重大催化或利空" not in report
    assert "**舆情/新闻**：近期新闻证据不足，无法可靠判断当前舆情方向或是否存在新增催化/利空。" in report
    assert "**数据可用性**：近期新闻/催化证据不足；盘中技术数据不可用；实时行情存在降级/回退。" in report

    email = build_investor_email_markdown(report)
    assert "# 美股决策日报 · 2026-08-11" in email
    assert "**当前执行授权**：**否**" in email
    assert "**当前可执行仓位上限**：**0.0%**" in email
    assert "**条件触发后最大仓位上限**：**5.0%**" in email
    assert "**保留入场区间（当前不可执行）**" in email
    assert "（条件参考，当前未获执行授权）" in email
    assert "当前执行 **观望**" not in email
    assert "**上游投研摘要（仅解释，非执行）**" in email
    assert "**投研摘要（原始观点）**" not in email
    assert "市场舆情平静，暂无重大催化或利空" not in email


def test_email_card_standardization_preserves_heading_boundaries() -> None:
    source = (
        "### 1. AAA · Alpha · 最终：观察 · 方向一致\n"
        "- **数据限制**：quote: fallback\n"
        "### 2. BBB · Beta · 最终：等待 · 部分一致\n"
        "- **数据限制**：新闻证据缺失\n"
        "## 4. 大模型与数据健康度\n"
    )

    normalized = _standardize_stock_cards(source)

    assert "quote: fallback\n\n### 2. BBB" in normalized
    assert "新闻证据缺失\n\n## 4. 大模型与数据健康度" in normalized
    assert "fallback### 2." not in normalized
    assert "新闻证据缺失## 4." not in normalized


def test_upstream_reduce_never_masquerades_as_final_wait_execution() -> None:
    payload, record = _googl_wait_case()
    raw_report = render_integrated_chinese_report(
        payload,
        v4_records=[record],
        report_date="2026-08-12",
    )
    packets = build_final_decision_packets(payload, v4_records=[record])

    report = apply_final_decision_contract(raw_report, packets)

    assert "最终：等待" in report
    assert "**是否值得买**：**暂不买，等待确认**" in report
    assert "当前执行 **减仓**" not in report
    assert "上游投研动作 **减仓**（非最终执行）" in report
    assert "**当前执行授权**：**否**" in report
    assert "**当前可执行仓位上限**：**0.0%**" in report
    assert_final_decision_report_consistency(report, packets)


def test_stale_final_business_verdict_still_fails_closed() -> None:
    payload = _payload()
    records = [_record()]
    report = render_integrated_chinese_report(
        payload,
        v4_records=records,
        report_date="2026-08-12",
    )
    stale = report.replace(
        "**是否值得买**：**条件式可买**",
        "**是否值得买**：**暂不买，等待确认**",
        1,
    )
    packets = build_final_decision_packets(payload, v4_records=records)

    with pytest.raises(ValueError, match="final verdict drift"):
        apply_final_decision_contract(stale, packets)


def test_raw_unstandardized_report_fails_contract_validation() -> None:
    payload = _payload()
    records = [_record()]
    report = render_integrated_chinese_report(
        payload,
        v4_records=records,
        report_date="2026-08-12",
    )
    packets = build_final_decision_packets(payload, v4_records=records)

    with pytest.raises(ValueError, match="effective trade date drift|execution authorization drift"):
        assert_final_decision_report_consistency(report, packets)


def test_missing_v4_gets_explicit_non_final_and_zero_execution() -> None:
    payload = _payload()
    raw_report = render_integrated_chinese_report(
        payload,
        v4_records=[],
        report_date="2026-08-12",
    )
    packets = build_final_decision_packets(payload, v4_records=[])
    report = apply_final_decision_contract(raw_report, packets)

    assert "**是否值得买**：**数据不足，不能形成最终买入判断**" in report
    assert "V4结构化投研缺失" in report
    assert "| 1 | MSFT | 等待（V4缺失） | V4结构化数据缺失 |" in report
    assert "**当前执行授权**：**否**" in report
    assert "**当前可执行仓位上限**：**0.0%**" in report
    assert_final_decision_report_consistency(report, packets)