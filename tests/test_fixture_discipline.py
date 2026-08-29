"""A fixture must not supply a timestamp that the database is going to compare against its own.

`CheckoutAuthority` carries `used_at >= created_at` as a CHECK constraint, and `created_at` is
`server_default=func.now()` - stamped by Postgres, from Postgres's clock. A fixture passing
`used_at=datetime.now(UTC)` supplies the *host* clock instead, so the constraint holds only while
two machines agree about the time.

They do not. While diagnosing this, the Docker Desktop container clock was measured 854ms ahead of
the Windows host, and it drifts: seven test files carried the fault and it surfaced as one
intermittent failure in a concurrency test that had nothing to do with concurrency. It cost a
reproduction loop to find, because the failure was rare and the assertion that caught it was
several layers away from the insert that caused it.

`func.now()` is evaluated by Postgres as the transaction start time, so both columns come from one
clock. This test keeps it that way.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Columns whose value the database also generates or compares against a generated one. A Python
# datetime for any of these is a bet on two clocks agreeing.
_SERVER_COMPARED_COLUMNS = ("used_at", "revoked_at", "released_at")

# `= datetime.now(` was too narrow: `revoked_at=at or datetime.now(UTC)` is the same mistake
# wearing an override, and that is exactly the form the delegation module was written in.
# Anchoring to the start of a line only ever matched fixture keyword arguments. The delegation
# module writes `.values(revoked_at=at or datetime.now(UTC))`, where the column sits mid-line
# behind a call - so the guard passed over the exact code it was being widened to cover. Verified
# by reintroducing the fault and watching this fail.
_HOST_CLOCK = re.compile(
    r"(?<![\w.])("
    + "|".join(_SERVER_COMPARED_COLUMNS)
    + r")\s*=\s*(?:[a-z_]+\s+or\s+)?datetime\.now\("
)


def _python_sources() -> list[Path]:
    """Every package that ships, plus the tests, discovered rather than listed.

    The previous version named five directories and `delegation` was not among them, so the module
    that reintroduced this exact bug was never looked at. A hardcoded list of places to check is a
    list that goes stale the first time someone adds a package - which has now happened three times
    in this repository, to the mypy configuration, the locking search, and here.
    """

    roots = [REPO_ROOT / "tests"] + [
        path.parent for path in REPO_ROOT.glob("*/__init__.py") if path.parent.name != "tests"
    ]
    return sorted(
        path
        for root in roots
        for path in root.rglob("*.py")
        # This file states the fault in order to forbid it, so it always matches itself.
        if "__pycache__" not in path.parts and path.name != Path(__file__).name
    )


def test_no_fixture_sets_a_server_compared_timestamp_from_the_host_clock() -> None:
    """Fails at the moment someone writes it, rather than once in twenty-five runs."""

    offenders = []
    for path in _python_sources():
        source = path.read_text(encoding="utf-8")
        match = _HOST_CLOCK.search(source)
        if match is not None:
            line = source[: match.start()].count(chr(10)) + 1
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")

    assert not offenders, (
        "these set a timestamp from the host clock that Postgres compares against its own "
        f"server-generated one; use func.now() instead: {offenders}"
    )


def test_the_constraint_this_protects_still_exists() -> None:
    """If the CHECK is ever dropped, this rule is guarding nothing and should be reconsidered.

    A test that outlives its reason is worse than no test: it constrains future work for a purpose
    nobody can reconstruct.
    """

    models = (REPO_ROOT / "models" / "domain.py").read_text(encoding="utf-8")

    assert "ck_checkout_authority_use_after_creation" in models
    assert "used_at IS NULL OR used_at >= created_at" in models
    assert "ck_delegation_revocation_after_creation" in models
    assert "revoked_at IS NULL OR revoked_at >= created_at" in models
    assert "ck_delegation_spend_release_after_creation" in models
    assert "released_at IS NULL OR released_at >= created_at" in models


def test_the_search_covers_delegation() -> None:
    """The module that reintroduced the bug must be inside the net that catches it.

    Named explicitly rather than trusted to the glob, because the glob is what silently excluded it
    last time.
    """

    scanned = {path.relative_to(REPO_ROOT).parts[0] for path in _python_sources()}

    assert "delegation" in scanned
    assert {"tests", "agent", "api", "models", "policy_engine", "state_machine"} <= scanned
