"""Fail CI when a non-test script has a broken import.

``python -m compileall`` byte-compiles without resolving names, and the
regression suite only runs ``scripts/test_*.py``. A typo in a script import
therefore shipped green (#23, same class as #9).

Scripts that guard their body with ``if __name__ == "__main__":`` are imported
via importlib (no ``__main__`` run). Scripts that execute at import time are
only AST-scanned so this file writes nothing under ``docs/assets/`` and
touches no network. The script list is the ``scripts/*.py`` directory minus
``test_*.py``, so a new script is covered the day it lands.

Usage:
    PYTHONPATH=. python scripts/test_script_imports.py
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SCRIPTS_DIR = Path(__file__).resolve().parent

_FAILURES: list[tuple[str, str]] = []
_N = 0


def _run(name: str, fn) -> None:
    global _N
    _N += 1
    try:
        fn()
        print(f"  PASS  {name}")
    except AssertionError as e:
        _FAILURES.append((name, str(e)))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001 — surface the script that crashed
        _FAILURES.append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def _non_test_scripts() -> list[Path]:
    return sorted(
        p
        for p in SCRIPTS_DIR.glob("*.py")
        if p.is_file() and not p.name.startswith("test_")
    )


def _is_main_compare(node: ast.expr) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not isinstance(node.ops[0], ast.Eq):
        return False

    def is_name(n: ast.expr) -> bool:
        return isinstance(n, ast.Name) and n.id == "__name__"

    def is_main(n: ast.expr) -> bool:
        return isinstance(n, ast.Constant) and n.value == "__main__"

    left, right = node.left, node.comparators[0]
    return (is_name(left) and is_main(right)) or (is_main(left) and is_name(right))


def _has_main_guard(tree: ast.AST) -> bool:
    for node in tree.body:
        if isinstance(node, ast.If) and _is_main_compare(node.test):
            return True
    return False


def _top_level_imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                names.append(node.module)
    return names


def _import_script(path: Path) -> None:
    module_name = f"_ai_crypto_script_import_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"no spec for {path.name}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


def _assert_imports_resolvable(path: Path, tree: ast.AST) -> None:
    missing = []
    for name in _top_level_imported_modules(tree):
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    assert not missing, f"{path.name} imports missing modules: {missing}"


def test_every_non_test_script_is_checked() -> None:
    scripts = _non_test_scripts()
    assert scripts, "scripts/ has no non-test Python files"
    names = {p.name for p in scripts}
    assert "plot_gross_edge.py" in names
    assert "plot_social_preview.py" in names
    assert "test_script_imports.py" not in names


def test_plot_social_preview_is_not_imported() -> None:
    """Guard against a later change that would execute this script in CI."""
    source = (SCRIPTS_DIR / "plot_social_preview.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="plot_social_preview.py")
    assert not _has_main_guard(tree)


def _check_one(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    if _has_main_guard(tree):
        _import_script(path)
    else:
        _assert_imports_resolvable(path, tree)


def main() -> None:
    print("-- Script imports --")
    _run("script list is derived from scripts/", test_every_non_test_script_is_checked)
    _run("plot_social_preview is AST-only", test_plot_social_preview_is_not_imported)
    for path in _non_test_scripts():
        _run(path.name, lambda p=path: _check_one(p))

    print(f"\n=== {_N - len(_FAILURES)}/{_N} PASS ===")
    if _FAILURES:
        print("\nFAILED:")
        for name, msg in _FAILURES:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
