"""A row lock must be taken through `models.locking.locked` and nowhere else.

This is a source-level assertion rather than a behavioural one, and deliberately so. The defect it
prevents is invisible at every individual call site: `SELECT ... FOR UPDATE` acquires the lock
correctly and then SQLAlchemy discards the row it returned in favour of one already in the identity
map, so the code reads as correct and waits for real while deciding from state the lock existed to
hide. Whether that is exploitable at any given site depends on whether some earlier line in the
same request happened to load that row, which is not a property anyone can hold in their head while
reviewing a diff.

`locked()` pairs the lock with `populate_existing` permanently, and this test makes the pairing the
only way to lock. It fails on a new direct `.with_for_update(` the moment one is written.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEARCHED_PACKAGES = (
    "api",
    "agent",
    "mcp_server",
    "mock_provider",
    "models",
    "policy_engine",
    "schemas",
    "state_machine",
)
LOCKING_HELPER = REPO_ROOT / "models" / "locking.py"


def _source_files() -> list[Path]:
    return sorted(
        path
        for package in SEARCHED_PACKAGES
        for path in (REPO_ROOT / package).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_the_locking_helper_is_the_only_place_a_row_lock_is_taken() -> None:
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in _source_files()
        if path != LOCKING_HELPER and ".with_for_update(" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "these files lock rows directly instead of through models.locking.locked, so the lock "
        f"returns a row the ORM may discard: {offenders}"
    )


def test_the_locking_helper_pairs_the_lock_with_a_fresh_read() -> None:
    source = LOCKING_HELPER.read_text(encoding="utf-8")

    assert ".with_for_update()" in source
    assert "populate_existing=True" in source, (
        "locked() no longer forces the locked row to overwrite the identity map, which silently "
        "returns every caller to deciding from stale state"
    )


def test_the_search_covers_every_package_that_holds_source() -> None:
    """A package added later must not quietly fall outside the check above."""

    packages_on_disk = {
        path.name
        for path in REPO_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").exists() and not path.name.startswith(".")
    }
    unsearched = packages_on_disk - set(SEARCHED_PACKAGES) - {"tests", "scenarios", "alembic"}

    assert not unsearched, f"add these packages to SEARCHED_PACKAGES: {sorted(unsearched)}"
