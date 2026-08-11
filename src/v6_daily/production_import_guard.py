from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Iterable


PRODUCTION_IMPORT_GUARD_SCHEMA_VERSION = "v6-production-import-guard-v1"
FORBIDDEN_MODULE_PREFIXES = (
    "scripts.run_v6_daily",
    "scripts.run_v6_daily_stage9",
    "scripts.run_v6_daily_stage10",
    "src.v6_daily.store",
    "src.v6_daily.versioned_store",
    "src.v6_daily.canonical_write_store",
    "src.v6_daily.normalized_write_store",
    "src.v6_daily.normalized_read_store",
    "src.v6_daily.normalized_persistence",
    "src.v6_daily.read_cutover",
)
DEFAULT_ENTRY_MODULE = "scripts.run_v6_daily_stage11"


def _module_to_path(repo_root: Path, module: str) -> Path | None:
    relative = Path(*module.split("."))
    candidate = repo_root / relative.with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = repo_root / relative / "__init__.py"
    return package if package.is_file() else None


def _path_to_module(repo_root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(repo_root.resolve())
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(parts)


def _resolve_relative(current_module: str, level: int, module: str | None) -> str:
    parts = current_module.split(".")
    package = parts[:-1]
    keep = len(package) - max(0, level - 1)
    base = package[: max(0, keep)]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _local_imports(repo_root: Path, path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    current = _path_to_module(repo_root, path)
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(current, node.level, node.module)
            else:
                base = str(node.module or "")
            if base:
                result.append(base)
    return result


def evaluate_production_import_graph(
    repo_root: str | Path,
    *,
    entry_module: str = DEFAULT_ENTRY_MODULE,
    forbidden_prefixes: Iterable[str] = FORBIDDEN_MODULE_PREFIXES,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    forbidden = tuple(str(item).strip() for item in forbidden_prefixes if str(item).strip())
    queue = [entry_module]
    visited: set[str] = set()
    edges: list[Dict[str, str]] = []
    violations: list[Dict[str, str]] = []

    while queue:
        module = queue.pop(0)
        if module in visited:
            continue
        visited.add(module)
        path = _module_to_path(root, module)
        if path is None:
            continue
        for imported in _local_imports(root, path):
            edges.append({"from": module, "to": imported})
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden):
                violations.append({"from": module, "to": imported})
                continue
            if imported.startswith("src.v6_daily.") or imported.startswith("scripts."):
                if _module_to_path(root, imported) is not None and imported not in visited:
                    queue.append(imported)

    return {
        "schema_version": PRODUCTION_IMPORT_GUARD_SCHEMA_VERSION,
        "status": "clean" if not violations else "blocked",
        "entry_module": entry_module,
        "visited_module_count": len(visited),
        "edge_count": len(edges),
        "forbidden_import_count": len(violations),
        "forbidden_modules": list(forbidden),
        "violations": violations,
        "visited_modules": sorted(visited),
    }


def assert_production_import_graph_clean(
    repo_root: str | Path,
    *,
    entry_module: str = DEFAULT_ENTRY_MODULE,
) -> Dict[str, Any]:
    result = evaluate_production_import_graph(repo_root, entry_module=entry_module)
    if result["status"] != "clean":
        raise RuntimeError(
            "V6 production import graph reaches retired legacy runtime modules: "
            + repr(result.get("violations"))
        )
    return result
