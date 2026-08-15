#!/usr/bin/env python3
"""Fail a pull request that removes a public definition from ``src/`` without
naming that definition in the PR description.

Three prose layers already ask contributors to keep a PR inside the behaviour it
claims to fix -- the scope rule in ``CONTRIBUTING.md``, the files-touched question
in the PR template, and the ``Scope`` section of each issue. All three were
violated by the same PR, so the check now lives in CI, where reading is not
optional.

Usage:

    PR_BODY="<pull request description>" python scripts/scope_guard.py <base-sha> <head-sha>

Exit code 0 when every removed public name is mentioned in ``PR_BODY``, 1 otherwise.
A name counts as public when it is defined at module level and does not start
with an underscore.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys

WATCHED_PREFIX = "src/"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def blob(ref: str, path: str) -> str | None:
    """Return the file content at ``ref``, or None when it does not exist there."""
    result = git("show", f"{ref}:{path}")
    return result.stdout if result.returncode == 0 else None


def public_names(source: str, origin: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        print(f"scope-guard: cannot parse {origin} ({exc}); treating it as empty.")
        return set()
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
    return names


def changed_python_files(base: str, head: str) -> list[str]:
    result = git("diff", "--name-only", f"{base}...{head}", "--", f"{WATCHED_PREFIX}*.py")
    if result.returncode != 0:
        print(f"scope-guard: git diff failed: {result.stderr.strip()}")
        sys.exit(1)
    return [line for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    base, head = argv[1], argv[2]
    body = os.environ.get("PR_BODY", "")

    removals: list[tuple[str, str]] = []
    for path in changed_python_files(base, head):
        before = blob(base, path)
        if before is None:
            continue  # new file; nothing can have been removed from it
        after = blob(head, path)
        gone = public_names(before, f"{base}:{path}")
        if after is not None:
            gone -= public_names(after, f"{head}:{path}")
        for name in sorted(gone):
            removals.append((path, name))

    if not removals:
        print("scope-guard: no public definition removed from src/.")
        return 0

    unexplained = [(path, name) for path, name in removals if name not in body]
    for path, name in removals:
        state = "NOT MENTIONED" if (path, name) in unexplained else "mentioned"
        print(f"scope-guard: {path} removes {name}() -- {state} in the PR description.")

    if not unexplained:
        print(f"scope-guard: all {len(removals)} removal(s) are named in the PR description.")
        return 0

    print()
    print("A pull request may remove a public definition, but the description has to say so.")
    print("Either restore the definitions listed as NOT MENTIONED, or name each of them in")
    print("the PR description together with the reason it is going away.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
