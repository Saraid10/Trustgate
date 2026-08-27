"""Shipped code must not depend on `assert`, and the harness must be proven not to.

`python -O` strips assertion statements at compile time. Code that enforces a rule with `assert`
therefore enforces nothing under optimisation, and - worse - reports success. The scenario harness
raises `ScenarioViolation` explicitly for exactly this reason.

That reasoning sat in a docstring for the whole project and was never tested. Reintroducing the
mistake proved it matters: replacing the harness's five explicit raises with asserts and running
`python -O -m pytest tests/test_scenario_harness.py` produces `DID NOT RAISE` on every violation
case. The checks do not weaken under optimisation; they disappear.

`make verify-optimized` runs the harness and Tier A suites optimized, and CI runs it on every push.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Everything that ships and enforces something. Tests are excluded: pytest rewrites assertions in
# test modules into explicit raises, so theirs survive optimisation and are safe to keep.
SHIPPED_PACKAGES = (
    "api",
    "agent",
    "demo",
    "mcp_server",
    "mock_provider",
    "models",
    "policy_engine",
    "scenarios",
    "schemas",
    "state_machine",
)


def _asserts_in(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def test_no_shipped_module_enforces_anything_with_an_assert() -> None:
    """Under `python -O` an assert is not a weaker check. It is no check."""

    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line}"
        for package in SHIPPED_PACKAGES
        for path in sorted((REPO_ROOT / package).rglob("*.py"))
        if "__pycache__" not in path.parts
        for line in _asserts_in(path)
    ]

    assert not offenders, (
        "these use `assert` in shipped code, which python -O removes entirely: "
        f"{offenders}. Raise explicitly instead."
    )


def test_the_harness_raises_explicitly_rather_than_asserting() -> None:
    """The specific module whose design rests on this, checked directly."""

    harness = REPO_ROOT / "scenarios" / "tier_a" / "harness.py"
    source = harness.read_text(encoding="utf-8")

    assert not _asserts_in(harness), "the harness went back to using assert"
    assert "raise ScenarioViolation(" in source
    assert source.count("raise ScenarioViolation(") >= 5


def test_the_optimized_run_is_wired_into_the_gate() -> None:
    """A check nobody runs is a check that does not exist.

    The reasoning behind `ScenarioViolation` was correct and untested for the whole project. This
    asserts the verification is reachable from both the local gate and CI.
    """

    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "verify-optimized:" in makefile, "make verify-optimized is gone"
    assert "-O -m pytest" in makefile, "the make target stopped running optimized"
    assert "-O -m pytest" in workflow, "CI stopped running the optimized suite"
