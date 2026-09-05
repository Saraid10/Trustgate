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


CUTS = ("A", "B", "C", "D")


def _spoken_words(*, applying: str = "") -> int:
    """Words in a blockquote under a timed beat heading, with the named cuts taken.

    `applying="ABCD"` measures the shortest take the script offers; the default measures the
    longest. The markers themselves never count either way.
    """

    total = 0
    for block in re.split(r"\n## ", _pitch()):
        if not re.match(r"\d:\d\d", block.splitlines()[0].strip()):
            continue
        spoken = " ".join(line[1:].strip() for line in block.splitlines() if line.startswith(">"))
        for cut in applying:
            spoken = re.sub(
                rf"`?\[CUT {cut} \u2192`?.*?`?\u2190 CUT {cut}\]`?", "", spoken, flags=re.S
            )
        total += len(re.sub(r"`?\[CUT [A-D] \u2192`?|`?\u2190 CUT [A-D]\]`?", "", spoken).split())
    return total


def _minutes(words: int) -> float:
    """Speech at 150 words a minute, plus the twenty seconds the beats budget for silence."""

    return (words / 150 * 60 + 20) / 60


def test_the_length_it_claims_is_the_length_it_is() -> None:
    """The reason this file was rewritten: it said 600 words while holding 1,234.

    A script that lies about its own runtime is worse than one with no figure at all, because the
    figure is what you plan the recording around. Nobody notices the drift until a take runs long,
    and by then the tunnel is up and the database is seeded.
    """

    claimed = re.search(r"is \*\*(\d{3,4}) words\*\*", _pitch())

    assert claimed, "the pitch no longer states its own spoken word count"
    actual = _spoken_words()
    # Wide enough that reflowing a line is not a failure, narrow enough that adding or removing a
    # sentence is. Twenty words is about eight seconds of speech.
    assert abs(int(claimed.group(1)) - actual) <= 20, (
        f"the pitch claims {claimed.group(1)} spoken words; it holds {actual}"
    )


def test_the_pace_table_is_arithmetically_true() -> None:
    """How long this takes depends on the reader, so the file publishes a table instead of a number.

    Bounding the script at one assumed speaking rate was the wrong assertion: the same words are a
    six-minute video read deliberately and a four-minute one read fast, and the reader is the only
    one who knows which they are. What must not happen is the table drifting away from the text it
    describes - someone plans a recording around those figures, and a wrong one is discovered with
    the tunnel up and the database seeded.
    """

    for cut in CUTS:
        assert f"[CUT {cut}" in _pitch(), f"CUT {cut} is gone, so the short take no longer exists"

    rows = re.findall(
        r"\|[^|\n]*?(\d{3}) wpm[^|\n]*\|\s*\**(\d):(\d\d)\**\s*\|\s*\**(\d):(\d\d)\**\s*\|",
        _pitch(),
    )
    assert len(rows) >= 3, "the pace table is gone or no longer parses"

    # Not named `cut`: the loop above binds that to a cut's letter, and reusing it here would make
    # the name mean a string in one half of the function and a word count in the other.
    full, shortest = _spoken_words(), _spoken_words(applying="".join(CUTS))
    for wpm, fm, fs, cm, cs in rows:
        for words, minute, second, label in (
            (full, fm, fs, "as written"),
            (shortest, cm, cs, "cut"),
        ):
            stated = int(minute) * 60 + int(second)
            actual = words / int(wpm) * 60
            # Ten seconds of slack: the figures are rounded for a reader, not computed for a test.
            assert abs(stated - actual) <= 10, (
                f"at {wpm} wpm the table says {minute}:{second} {label}, "
                f"but {words} words is {actual / 60:.0f}:{actual % 60:02.0f}"
            )

    # The cuts still have to be worth taking. Under half a minute saved and they are decoration.
    saved = _minutes(full) - _minutes(shortest)
    assert saved >= 0.5, f"the cuts only save {saved * 60:.0f} seconds; that is not a cut list"


def test_the_architecture_page_it_tells_you_to_open_exists_and_is_true() -> None:
    """The architecture beat is a page, not a command, so nothing else would notice it rotting.

    It restates facts that live in the code - the five tool names, the enforcement ladder - and a
    diagram that has drifted from the system is worse than no diagram, because it is on screen for
    a minute while the narration vouches for it.
    """

    page = REPO_ROOT / "demo" / "architecture.html"

    assert page.is_file(), "demo/pitch.md sends you to demo/architecture.html, which is missing"
    assert "architecture.html" in _pitch(), "the pitch stopped pointing at the architecture page"

    html = page.read_text(encoding="utf-8")

    server = (REPO_ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8")
    tools = set(re.findall(r"^    async def (\w+)\(", server, re.MULTILINE))
    assert len(tools) == 5, f"the tool surface changed: {sorted(tools)}"
    for tool in tools:
        assert tool in html, f"the architecture page does not name the tool {tool!r}"

    # The ladder is quoted from docs/architecture.md. Both must keep saying the same thing.
    for invariant in ("composite", "partial unique index", "trigger", "raw bytes"):
        assert invariant.casefold() in html.casefold(), f"the page lost: {invariant!r}"

    # Self-contained: it is opened from the filesystem with no server and possibly no network.
    assert "http://" not in html and "https://" not in html, (
        "the architecture page fetches something remote; it must render offline from file://"
    )


def test_the_cut_table_costs_what_it_says_it_costs() -> None:
    """Third time the figures in this file went stale, so they get a test rather than a proofread.

    The cut list is read under pressure - a rehearsal has run long and something has to go. A row
    claiming 33 words that actually saves 23 sends you to cut two more things you did not need to.
    """

    table = _pitch().rsplit("## The cuts, in order", maxsplit=1)[-1]
    full = _spoken_words()

    rows = re.findall(r"\| \*\*([A-D])\*\* \|[^|]*\|\s*(\d+) w\s*\|", table)
    assert len(rows) == len(CUTS), f"the cut table lost rows: {rows}"
    for name, claimed in rows:
        actual = full - _spoken_words(applying=name)
        assert int(claimed) == actual, (
            f"cut {name} is listed at {claimed} words but removes {actual}"
        )

    # And the two running totals in the sentence above the table.
    for pattern, applied in ((r"words to (\d+)", "ABC"), (r"Adding D gives (\d+)", "ABCD")):
        stated = re.search(pattern, table)
        assert stated, f"the running total for {applied} is gone"
        actual = _spoken_words(applying=applied)
        assert int(stated.group(1)) == actual, (
            f"the table says {stated.group(1)} words with {applied} taken; it is {actual}"
        )
