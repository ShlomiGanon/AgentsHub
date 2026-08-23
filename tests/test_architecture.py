"""Import-graph check (work_plan.md §1.1).

Fails the build the day a package imports a module of another package that
is not that package's declared entry point. Mirrors docs/allowed_calls.md —
if the two ever disagree, this test is wrong and must be updated to match
that document, not the other way around.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# One entry: package name -> the exact dotted module names other packages
# may import from it. Empty set means "no other package may import this
# one at all" (cli).
ENTRY_POINTS: dict[str, set[str]] = {
    "persistence": {"persistence.interface", "persistence.exceptions"},
    "config": {"config.base", "config.settings_store"},
    "auth": {"auth.permissions"},
    "registries": {"registries.event_types", "registries.areas"},
    "profiles": {"profiles.loader", "profiles.spec"},
    "tools": {"tools.logging_config", "tools.tracing"},
    "cli": set(),
    "agents": {"agents.registry", "agents.results", "agents.errors", "agents.reference", "agents.history"},
    "protocols": {"protocols.model", "protocols.loader"},
    "history": {"history.interface", "history.query"},
    "agents": {"agents.registry", "agents.results", "agents.errors", "agents.reference"},
    "agents": {"agents.registry", "agents.results", "agents.errors", "agents.reference", "agents.base"},
    "protocols": {"protocols.model", "protocols.loader", "protocols.editor", "protocols.executor"},
    "history": {"history.query"},
    "orchestrator": {"orchestrator.flows"},
    "api": {"api.app"},
    "bot": {"bot.app"},
}

# tools.logging_config / tools.tracing are the one deliberate exception:
# reachable from internal (non-entry-point) modules of every package too.
CROSS_CUTTING_ALWAYS_ALLOWED = {"tools.logging_config", "tools.tracing"}

GOVERNED_PACKAGES = set(ENTRY_POINTS)


def _iter_governed_python_files():
    for package in GOVERNED_PACKAGES:
        package_dir = REPO_ROOT / package
        if package_dir.is_dir():
            yield from package_dir.rglob("*.py")


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _imported_module_names(tree: ast.Module) -> list[str]:
    # A plain ast.walk would also collect imports inside `if TYPE_CHECKING:`
    # blocks — those are never taken at runtime and create no real
    # cross-package coupling, so they're pruned from the walk entirely
    # rather than flagged (see profiles/validate.py, protocols/retry.py,
    # protocols/executor.py for the pattern this exempts).
    names = []

    def _visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            return

        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module)
            # level > 0 is a relative import — always same-package, skip.

        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)
    return names


def _violations_for_file(file_path: Path) -> list[str]:
    owning_package = file_path.relative_to(REPO_ROOT).parts[0]
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))

    violations = []

    for imported in _imported_module_names(tree):
        imported_package = imported.split(".")[0]

        if imported_package == owning_package or imported_package not in GOVERNED_PACKAGES:
            continue

        if imported in CROSS_CUTTING_ALWAYS_ALLOWED:
            continue

        if imported not in ENTRY_POINTS[imported_package]:
            allowed = sorted(ENTRY_POINTS[imported_package]) or "(nothing — this package is not importable)"
            violations.append(
                f"{file_path.relative_to(REPO_ROOT)}: imports '{imported}' from "
                f"package '{imported_package}', which is not a declared entry point "
                f"(allowed: {allowed})"
            )

    return violations


def test_no_cross_package_imports_outside_entry_points():
    all_violations = []

    for file_path in _iter_governed_python_files():
        all_violations.extend(_violations_for_file(file_path))

    assert not all_violations, "\n".join(all_violations)
