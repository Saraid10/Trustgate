"""Every mutation must still point at the code it claims to break.

A mutation identifies its target by quoting the source. Reformat that line, indent it one level
deeper, rename a variable in it, and the quote stops matching - the mutation edits nothing, the
guarding tests pass against untouched code, and the registry goes on describing an invariant it is
no longer testing.

`scenarios/mutation.py` already reports this honestly, as ANCHOR MISSING rather than as a pass. The
problem is when: the mutation suite takes minutes and runs after the tests, so a stale anchor is
found late, by someone who has already moved on. This finds it in the ordinary gate instead.

It was written after moving one claim inside a savepoint indented its anchor out of existence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scenarios.mutation import MUTATIONS

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_every_mutation_still_matches_the_source_exactly_once(mutation: object) -> None:
    assert hasattr(mutation, "path"), "unexpected registry entry"
    target = REPO_ROOT / mutation.path  # type: ignore[attr-defined]

    assert target.is_file(), f"{mutation.path} is gone"  # type: ignore[attr-defined]

    occurrences = target.read_text(encoding="utf-8").count(
        mutation.original  # type: ignore[attr-defined]
    )

    assert occurrences == 1, (
        f"the anchor matches {occurrences} times, so this mutation tests nothing. "
        "Re-aim it at the code it is meant to break."
    )


def test_every_mutation_actually_changes_something() -> None:
    """A mutation whose replacement equals its original is a no-op that always looks caught."""

    inert = [mutation.name for mutation in MUTATIONS if mutation.original == mutation.mutated]

    assert not inert, f"these mutations change nothing: {inert}"


def test_mutation_names_are_unique() -> None:
    """Two entries sharing a name make a report impossible to act on."""

    names = [mutation.name for mutation in MUTATIONS]
    duplicates = sorted({name for name in names if names.count(name) > 1})

    assert not duplicates, f"duplicate mutation names: {duplicates}"
