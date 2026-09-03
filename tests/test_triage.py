"""The guided tour has to run on a machine nobody prepared, which is the whole point of it.

Two failure modes matter here and neither is about logic. A tour that raises before printing
anything is worse than no tour, and both ways that can happen have already happened in this
repository: a character the console cannot encode, and a step that assumes a database is running.

So this asserts the two properties that make it safe to hand to a stranger.
"""

from __future__ import annotations

from pathlib import Path

import scenarios.triage as triage

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_tour_is_pure_ascii_so_a_cp1252_console_can_print_it() -> None:
    """Windows consoles default to cp1252, and `print` raises rather than degrading.

    A box-drawing character in the separator crashed this module before it printed a single line -
    on the machine it was written on. A reader would have seen a UnicodeEncodeError traceback as
    their first impression of the project.
    """

    source = Path(triage.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
    offenders = sorted({character for character in source if ord(character) > 127})

    assert not offenders, (
        "scenarios/triage.py contains characters a cp1252 console cannot print: "
        f"{[(character, hex(ord(character))) for character in offenders]}"
    )


def test_the_first_two_steps_need_no_database() -> None:
    """A reader who has not started Docker still has to see the strongest thing.

    Step one runs `demo.unguarded`, which holds no database session, and step two reads a preserved
    file. Only the mutation step needs Postgres, and it says so and skips rather than raising.
    """

    source = Path(triage.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]

    problem = source.index("def _the_problem")
    proof = source.index("def _the_proof")

    # Asserted as what the steps *do*, not as words they avoid. An earlier version of this test
    # searched for "docker" and flagged the label that exists precisely to say Docker is not
    # needed - a check that would have forced the honest wording out of the tour to stay green.
    cold_steps = source[problem:proof]
    assert "SessionLocal" not in cold_steps, "a cold step opened a database session"
    assert "create_engine" not in cold_steps, "a cold step connected to the database"
    assert "_postgres_is_up" not in cold_steps, "a cold step gated itself on Postgres"
    assert "demo.unguarded" in cold_steps, "step one no longer runs the unguarded baseline"
    assert "read_text" in cold_steps, "step two no longer reads the preserved artifact"

    # And the step that does need it degrades rather than exploding.
    guarded = source[proof:]
    assert "if not postgres:" in guarded
    assert "docker compose up -d" in guarded


def test_the_evidence_step_reads_an_artifact_that_exists() -> None:
    """The tour quotes a real captured payment. A renamed file would make it quote nothing."""

    assert (REPO_ROOT / "docs" / "evidence" / "m3-provider-delivered-webhook.json").is_file()


def test_postgres_detection_answers_rather_than_raising() -> None:
    """Called before anything is printed, so it must return a bool under every condition."""

    assert isinstance(triage._postgres_is_up(), bool)
