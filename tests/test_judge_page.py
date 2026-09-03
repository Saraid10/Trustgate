"""`JUDGE.md` promises that every claim regenerates. A stale command breaks exactly that promise.

This is the one document written for someone who will run what it says rather than read past it,
which makes a renamed test file worse here than anywhere else in the repository: the reader does
not discover a typo, they discover that the project does not do what it claims.

So the counts, the file paths, the test names, and the commands are all checked against the code
they describe.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUDGE = REPO_ROOT / "JUDGE.md"


def _judge() -> str:
    return JUDGE.read_text(encoding="utf-8")


def test_the_page_exists() -> None:
    assert JUDGE.is_file(), "JUDGE.md is missing"


def test_every_test_file_it_names_exists() -> None:
    """A renamed test file turns a proof into a `file not found` in front of a reader."""

    named = sorted(set(re.findall(r"tests/(test_[a-z0-9_]+)\.py", _judge())))

    assert named, "JUDGE.md names no test files, which means it stopped citing evidence"
    for name in named:
        assert (REPO_ROOT / "tests" / f"{name}.py").is_file(), (
            f"JUDGE.md cites tests/{name}.py, which does not exist"
        )


def test_every_named_test_function_exists() -> None:
    """The page points at individual tests by name. Those are the strongest citations it makes."""

    suite = "\n".join(
        path.read_text(encoding="utf-8") for path in (REPO_ROOT / "tests").glob("test_*.py")
    )
    named = sorted(set(re.findall(r"`(test_[a-z0-9_]{10,})`", _judge())))

    for name in named:
        assert f"def {name}" in suite, f"JUDGE.md cites `{name}`, which no test defines"


def test_every_module_it_tells_you_to_run_exists() -> None:
    """Same reasoning as the demo script: a renamed module should fail here, not in front
    of the person the page was written for."""

    for module in sorted(set(re.findall(r"python -m ([a-z_][a-z0-9_.]*)", _judge()))):
        assert importlib.util.find_spec(module) is not None, (
            f"JUDGE.md runs `{module}`, which is gone"
        )


def test_every_document_it_links_exists() -> None:
    for link in sorted(set(re.findall(r"\]\((?!http)([^)#]+\.md)\)", _judge()))):
        assert (REPO_ROOT / link).is_file(), f"JUDGE.md links {link}, which does not exist"


def test_every_evidence_artifact_it_lists_exists() -> None:
    """The page offers preserved evidence by filename. A missing one reads as a fabricated claim."""

    for artifact in sorted(set(re.findall(r"`(docs/evidence/[a-z0-9\-*.]+\.json)`", _judge()))):
        if "*" in artifact:
            pattern = Path(artifact).name
            assert list((REPO_ROOT / "docs" / "evidence").glob(pattern)), (
                f"JUDGE.md lists {artifact}, which matches no file"
            )
            continue
        assert (REPO_ROOT / artifact).is_file(), f"JUDGE.md lists {artifact}, which is missing"


def test_the_mutation_count_matches_the_registry() -> None:
    """The number a reader is most likely to check, because it is the boldest claim on the page."""

    from scenarios.mutation import MUTATIONS

    stated = re.findall(r"\*\*(\d+) guards deleted", _judge()) + re.findall(
        r"holds \*\*(\d+) deliberate breaks", _judge()
    )

    assert stated, "JUDGE.md no longer states how many guards the mutation registry holds"
    for number in stated:
        assert int(number) == len(MUTATIONS), (
            f"JUDGE.md says {number} mutations; the registry holds {len(MUTATIONS)}"
        )


def test_the_scenario_count_matches_the_registry() -> None:
    from scenarios.tier_a import REGISTRY

    stated = re.findall(r"(\d+) adversarial scenarios", _judge())

    assert stated, "JUDGE.md no longer states how many adversarial scenarios exist"
    for number in stated:
        assert int(number) == len(REGISTRY), (
            f"JUDGE.md says {number} scenarios; the registry holds {len(REGISTRY)}"
        )


def test_it_still_states_the_limitations() -> None:
    """The section that makes the rest of the page credible, and the easiest one to quietly trim."""

    page = _judge().casefold()

    for cut in ("test mode only", "no agent identity", "not tamper-evident", "no rate limiting"):
        assert cut in page, f"JUDGE.md stopped stating the limitation: {cut!r}"
