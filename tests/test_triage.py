"""The guided tour has to run on a machine nobody prepared, which is the whole point of it.

Two failure modes matter here and neither is about logic. A tour that raises before printing
anything is worse than no tour, and both ways that can happen have already happened in this
repository: a character the console cannot encode, and a step that assumes a database is running.

So this asserts the two properties that make it safe to hand to a stranger.
"""

from __future__ import annotations

import sys
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


# --- the step that cannot run --------------------------------------------------------------


def test_a_step_that_cannot_start_says_so_instead_of_printing_nothing() -> None:
    """The failure a reader actually hit, and the worst possible one for this command.

    The first version captured stdout and ignored both the exit code and stderr. In an environment
    missing a dependency, `demo.unguarded` died on an import, produced no stdout, and the tour
    printed a heading followed by blank space - reporting a failed step as a quiet success. The
    person running it had no way to know anything was wrong, let alone what.
    """

    ran, lines = triage._run([sys.executable, "-c", "import a_module_that_is_not_installed"])

    assert ran is False
    assert lines, "a step that could not run printed nothing at all"

    told = " ".join(lines)
    assert "could not run" in told
    assert "a_module_that_is_not_installed" in told, "the actual cause was thrown away"
    assert 'pip install -e ".[dev]"' in told, "the reader was not told how to fix it"


def test_a_step_that_runs_and_reports_a_failure_is_not_mistaken_for_a_broken_environment() -> None:
    """`scenarios.mutation` exits non-zero when an invariant is unguarded.

    That is a finding this tour exists to show, not a reason to print an install hint. The two are
    told apart by whether the step produced output: a process that died on an import printed
    nothing, one that ran and disagreed printed its reasons.
    """

    ran, lines = triage._run(
        [sys.executable, "-c", "print('1 mutation survived'); raise SystemExit(1)"]
    )

    assert ran is True, "a step that ran and reported a finding was treated as a broken environment"
    assert "1 mutation survived" in " ".join(lines)


def test_a_successful_step_returns_its_own_output() -> None:
    ran, lines = triage._run([sys.executable, "-c", "print('hello from the step')"])

    assert ran is True
    assert lines == ["hello from the step"]
