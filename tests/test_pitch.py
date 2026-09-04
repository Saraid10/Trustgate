"""The spoken script says numbers out loud, next to a terminal printing them.

`test_demo_script.py` already learned this lesson: the closing beat said "seventeen deliberate
breaks" while `make mutation` printed thirty-seven, which would have put the narration in direct
contradiction with the screen beside it. The pitch has the same exposure and more of it, because
every figure in it is spoken rather than shown.

It also has to keep saying the things the submission asks for. A pitch that quietly loses the
limitations, or the account of what broke, is a worse pitch - those are the parts that make the
rest of it believable.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PITCH = REPO_ROOT / "demo" / "pitch.md"


def _pitch() -> str:
    return PITCH.read_text(encoding="utf-8")


def _footer() -> str:
    """The canonical line of figures, kept apart from prose that spells them out for speaking."""

    return _pitch().rsplit("## Numbers to have right", maxsplit=1)[-1]


def test_the_pitch_exists() -> None:
    assert PITCH.is_file(), "demo/pitch.md is missing"


def test_the_test_count_in_the_footer_matches_the_suite() -> None:
    """Checked in the footer rather than the prose, because the prose spells numbers out.

    The script is meant to be read aloud, so it says "five hundred and eighty-three" where a
    machine wants 583. Rather than teach a regex English, the file carries one canonical line of
    figures at the end - which is also the line to glance at before recording.
    """

    stated = re.findall(r"(\d{3}) tests", _footer())

    assert stated, "the footer no longer states how many tests there are"
    # Counted from the files rather than by running them: this test is itself one of the tests, so
    # asking pytest for the number from inside pytest would be circular.
    defined = sum(
        len(re.findall(r"^(?:async )?def test_", path.read_text(encoding="utf-8"), re.MULTILINE))
        for path in (REPO_ROOT / "tests").glob("test_*.py")
    )
    for number in stated:
        # Parametrised tests expand at collection, so the stated figure is the larger one. What
        # matters is that it has not drifted below the definitions actually on disk.
        assert int(number) >= defined, (
            f"the footer says {number} tests, but {defined} test functions are defined - "
            "the number has gone stale downward"
        )


def test_the_spoken_mutation_count_matches_the_registry() -> None:
    from scenarios.mutation import MUTATIONS

    stated = re.findall(r"(\d+) (?:deliberate breaks|safety guards|mutations)", _pitch())

    assert stated, "the pitch no longer states how many guards the mutation registry holds"
    for number in stated:
        assert int(number) == len(MUTATIONS), (
            f"the pitch says {number}; the registry holds {len(MUTATIONS)}"
        )


def test_the_spoken_scenario_count_matches_the_registry() -> None:
    from scenarios.tier_a import REGISTRY

    for number in re.findall(r"(\d+) adversarial scenarios", _pitch()):
        assert int(number) == len(REGISTRY), (
            f"the pitch says {number} scenarios; the registry holds {len(REGISTRY)}"
        )


def test_the_spoken_migration_count_matches_the_directory() -> None:
    on_disk = len(list((REPO_ROOT / "alembic" / "versions").glob("*.py")))

    for number in re.findall(r"(\d+) migrations", _pitch()):
        assert int(number) == on_disk, f"the pitch says {number} migrations; {on_disk} exist"


def test_the_tool_count_it_claims_is_the_tool_count_offered() -> None:
    """ "Five tools and none of them can pay" is the load-bearing architectural claim."""

    # Both forms appear on purpose: the spoken line says "five" and the footer says "5".
    stated = {claim.casefold() for claim in re.findall(r"(\w+) MCP tools", _pitch())}

    assert stated, "the pitch stopped saying how many tools the agent is offered"
    assert stated <= {"five", "5"}, f"unexpected tool count claim: {sorted(stated)}"


def test_every_command_it_tells_you_to_run_exists() -> None:
    """A renamed module should fail here rather than halfway through a take."""

    for module in sorted(set(re.findall(r"python -m ([a-z_][a-z0-9_.]*)", _pitch()))):
        assert importlib.util.find_spec(module) is not None, (
            f"the pitch runs `{module}`, which is gone"
        )


def test_it_still_covers_what_the_submission_asks_for() -> None:
    """The brief asks for specific things. Losing one in an edit is silent and expensive."""

    spoken = _pitch().casefold()

    for asked in ("what broke", "architectur", "audit trail", "gated", "bounded", "explainable"):
        assert asked in spoken, f"the pitch stopped covering: {asked!r}"


def test_it_still_states_the_limitations_out_loud() -> None:
    """The section that makes everything before it credible, and the first one cut for time."""

    spoken = _pitch().casefold()

    for cut in ("test mode only", "agent identity", "not real authentication"):
        assert cut in spoken, f"the pitch stopped saying: {cut!r}"


def test_it_still_refuses_the_claims_the_project_will_not_make() -> None:
    """The same words `demo/script.md` bans, banned here too.

    Nothing was blocked, detected, or caught. Saying otherwise on camera would describe the one
    thing this project specifically does not do.
    """

    spoken = _pitch().casefold()

    for forbidden in ("blocked the attack", "detected the attack", "caught the attack"):
        assert forbidden not in spoken, f"the pitch claims {forbidden!r}, which is not true"
