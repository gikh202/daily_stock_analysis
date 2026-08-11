from __future__ import annotations

import json
import re

from src.formatters import markdown_to_html_document
from src.llm.litellm_backend import LiteLLMGenerationBackend
from src.notification_sender.email_sender import _stabilize_html_tables
from src.v6_daily.v4_research_adapter import latest_v4_views, normalize_v4_record


def _record(code: str, intelligence: dict | None = None, *, history_id: int = 1) -> dict:
    return {
        "id": history_id,
        "code": code,
        "raw_result": json.dumps(
            {
                "code": code,
                "name": "大盘复盘" if code == "MARKET" else code,
                "dashboard": {"intelligence": intelligence or {}},
            },
            ensure_ascii=False,
        ),
    }


def test_v4_fusion_excludes_market_review_records() -> None:
    records = [
        _record("MSFT", history_id=1),
        _record("MARKET", history_id=2),
    ]

    views = latest_v4_views(records)

    assert set(views) == {"MSFT"}
    assert normalize_v4_record(records[1]) is None


def test_v4_fusion_drops_neutral_recent_evidence_placeholders() -> None:
    record = _record(
        "MSFT",
        {
            "latest_news": "暂无已验证的近期新闻证据。",
            "positive_catalysts": [
                "暂无已验证的近期证据",
                "2026-08-08 verified catalyst [E05]",
            ],
            "risk_alerts": [
                "暂无已验证的近期证据",
                "2026-08-10 verified risk [E01]",
            ],
        },
    )

    normalized = normalize_v4_record(record)

    assert normalized is not None
    assert normalized["latest_news"] == ""
    assert normalized["catalysts"] == ["2026-08-08 verified catalyst [E05]"]
    assert normalized["risks"] == ["2026-08-10 verified risk [E01]"]


def test_final_email_tables_keep_separate_header_cells() -> None:
    markdown = """### 今日动作

| 标的 | 动作 | 主预测 | 量化方向 | 机会 | 风险 |
|---|---|---|---|---:|---:|
| MSFT | 观察 | 10d 看多 | 看多 | 77.7 | 47.1 |
"""

    html = _stabilize_html_tables(markdown_to_html_document(markdown))

    assert 'style="display:table !important;' in html
    for heading in ("标的", "动作", "主预测", "量化方向", "机会", "风险"):
        assert re.search(rf"<th(?:\s[^>]*)?>{re.escape(heading)}</th>", html)


def test_structured_backend_repairs_json_locally_before_extra_model_call() -> None:
    calls: list[str] = []

    class ValidationFailure(RuntimeError):
        pass

    failure = ValidationFailure("invalid_json: malformed object")
    failure.last_response_text = '{"ok": true'
    failure.last_model = "deepseek/deepseek-v4-flash"
    failure.last_usage = {"provider": "deepseek"}

    def completion(prompt: str, generation_config: dict, **kwargs):
        calls.append(prompt)
        raise failure

    def validator(text: str) -> None:
        payload = json.loads(text)
        assert payload == {"ok": True}

    result = LiteLLMGenerationBackend(completion).generate(
        "return strict JSON",
        {"temperature": 0.7},
        response_validator=validator,
    )

    assert len(calls) == 1
    assert json.loads(result.text) == {"ok": True}
    assert result.model == "deepseek/deepseek-v4-flash"
    assert result.diagnostics["validator_repair_used"] is True
    assert result.diagnostics["validator_local_json_repair_used"] is True
