# -*- coding: utf-8 -*-
"""Repository-wide dependency direction and compatibility-size contracts."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path("src")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_does_not_depend_on_outer_architecture_layers() -> None:
    blocked_prefixes = (
        "src.application",
        "src.bootstrap",
        "src.infrastructure",
        "src.presentation",
        "src.core.pipeline",
        "src.analyzer",
        "fastapi",
        "litellm",
        "bot",
    )
    for path in (ROOT / "domain").rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith(blocked_prefixes), (
                f"domain dependency violation: {path} imports {module}"
            )


def test_historical_god_module_paths_are_thin_compatibility_surfaces() -> None:
    max_lines = {
        "src/analyzer.py": 120,
        "src/core/pipeline.py": 140,
        "src/config.py": 80,
        "src/core/config_registry.py": 80,
        "src/core/config_manager.py": 80,
        "src/core/pipeline_factory_registry.py": 40,
        "src/core/pipeline_dependencies.py": 40,
        "src/core/pipeline_optional_dependencies.py": 40,
    }
    for raw_path, limit in max_lines.items():
        path = Path(raw_path)
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        assert line_count <= limit, f"{raw_path} regrew to {line_count} lines"


def test_policy_and_stage_legacy_paths_are_forwarders_only() -> None:
    expected = {
        "src/analysis_content_integrity.py": "src.domain.analysis.content_integrity",
        "src/quote_presentation_policy.py": "src.presentation.policies.quote",
        "src/chip_presentation_policy.py": "src.presentation.policies.chip",
        "src/price_position_policy.py": "src.presentation.policies.price_position",
        "src/structural_decision_policy.py": "src.domain.decision.structural",
        "src/trend_prompt_policy.py": "src.infrastructure.llm.trend_prompt",
        "src/core/stages/market_data.py": "src.application.analysis.stages.market_data",
        "src/core/stages/volume_price.py": "src.application.analysis.stages.volume_price",
        "src/core/stages/decision_trace.py": "src.application.analysis.stages.decision_trace",
    }
    for raw_path, target in expected.items():
        source = Path(raw_path).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 16, f"legacy path regrew: {raw_path}"
        assert f'_TARGET = "{target}"' in source


def test_legacy_pipeline_module_lookup_is_quarantined() -> None:
    allowed = Path("src/bootstrap/legacy_pipeline_factory_seams.py")
    offenders = []
    for path in ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if 'sys.modules.get("src.core.pipeline")' in source and path != allowed:
            offenders.append(str(path))
    assert offenders == []


def test_canonical_architecture_roots_exist() -> None:
    required = [
        "src/domain/analysis/content_integrity.py",
        "src/domain/decision/structural.py",
        "src/application/analysis/pipeline_impl.py",
        "src/application/analysis/stages/decision_trace.py",
        "src/application/analysis/stages/market_data.py",
        "src/application/analysis/stages/volume_price.py",
        "src/infrastructure/llm/analyzer_impl.py",
        "src/infrastructure/llm/trend_prompt.py",
        "src/presentation/policies/chip.py",
        "src/presentation/policies/quote.py",
        "src/presentation/policies/price_position.py",
        "src/bootstrap/config_impl.py",
        "src/bootstrap/config_registry_impl.py",
        "src/bootstrap/config_manager_impl.py",
        "src/bootstrap/pipeline_factory_registry.py",
        "src/bootstrap/pipeline_dependencies.py",
    ]
    missing = [path for path in required if not Path(path).is_file()]
    assert missing == []
