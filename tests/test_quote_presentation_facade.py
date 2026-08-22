# -*- coding: utf-8 -*-
"""Contract tests for the analyzer quote-presentation compatibility facade."""

from __future__ import annotations

import ast
from pathlib import Path


_POLICY_ALIASES = {
    "_phase_aware_quote_labels": "_phase_aware_quote_labels_policy",
    "_should_hide_regular_session_ohlc": "_should_hide_regular_session_ohlc_policy",
    "_today_has_realtime_overlay": "_today_has_realtime_overlay_policy",
    "_today_looks_complete_daily_bar": "_today_looks_complete_daily_bar_policy",
}


def _top_level_functions(tree: ast.Module):
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in _POLICY_ALIASES
    }


def test_analyzer_imports_quote_presentation_policy_with_private_aliases() -> None:
    tree = ast.parse(
        Path("src/infrastructure/llm/analyzer_impl.py").read_text(encoding="utf-8")
    )
    imports = {
        alias.name: alias.asname
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.quote_presentation_policy"
        for alias in node.names
    }

    assert imports == _POLICY_ALIASES


def test_analyzer_keeps_thin_quote_presentation_facades() -> None:
    tree = ast.parse(
        Path("src/infrastructure/llm/analyzer_impl.py").read_text(encoding="utf-8")
    )
    functions = _top_level_functions(tree)

    assert set(functions) == set(_POLICY_ALIASES)
    for function_name, policy_alias in _POLICY_ALIASES.items():
        function = functions[function_name]
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == policy_alias
        ]
        assert len(calls) == 1, function_name

        control_flow = [
            node
            for node in ast.walk(function)
            if isinstance(
                node,
                (
                    ast.If,
                    ast.For,
                    ast.While,
                    ast.Try,
                    ast.Match,
                ),
            )
        ]
        assert control_flow == [], function_name
