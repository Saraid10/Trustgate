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
_SERVER_COMPARED_COLUMNS = ("used_at",)

_HOST_CLOCK = re.compile(
    r"^\s*(" + "|".join(_SERVER_COMPARED_COLUMNS) + r")\s*=\s*datetime\.now\(", re.MULTILINE
)


def _python_sources() -> list[Path]:
    return sorted(
        path
        for directory in ("tests", "agent", "api", "mcp_server", "scenarios")
        for path in (REPO_ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
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
