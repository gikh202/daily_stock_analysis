#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Baselines are intentionally close to the current files. The goal is to make
# further monolith growth a CI failure while refactors move responsibilities out
# incrementally without destabilising production behavior in one giant rewrite.
BUDGETS = {
    "src/core/pipeline.py": 218_000,
    "src/core/config_registry.py": 190_000,
    "data_provider/base.py": 174_000,
    "src/llm/local_cli_backend.py": 104_000,
    "src/agent/orchestrator.py": 87_000,
}


def main() -> None:
    failures: list[str] = []
    for relative, budget in BUDGETS.items():
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"missing architecture-budget file: {relative}")
            continue
        size = path.stat().st_size
        print(f"{relative}: {size} / {budget} bytes")
        if size > budget:
            failures.append(f"{relative} exceeds architecture budget: {size}>{budget}")
    if failures:
        raise SystemExit("Architecture budget failed: " + "; ".join(failures))
    print("Architecture budget: PASS")


if __name__ == "__main__":
    main()
