"""The README is the most-read file in the repository and the least likely to be run.

Its links and commands are checked here for the same reason the demo script's are: prose does not
fail loudly. A dead link or a renamed module keeps reading perfectly and stops being true, and the
person who finds out is a stranger evaluating the project.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


# Explicit encoding. These run at collection time on Windows, where the default is cp1252 and
# a single character outside it - a rupee sign, an em dash, a status glyph - turns a README
# edit into a collection error rather than a test failure, which is a much worse thing to debug.
_LINKED_FILES = sorted(
    set(re.findall(r"\]\((?!http)([^)#]+\.md)\)", README.read_text(encoding="utf-8")))
)
_COMMANDS = sorted(
    set(re.findall(r"python -m ([a-z_][a-z0-9_.]*)", README.read_text(encoding="utf-8")))
)


@pytest.mark.parametrize("relative", _LINKED_FILES)
def test_every_document_the_readme_links_exists(relative: str) -> None:
    assert (REPO_ROOT / relative).is_file(), f"the README links {relative!r}, which is not there"


@pytest.mark.parametrize("module", _COMMANDS)
def test_every_command_the_readme_names_is_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is not None, (
        f"the README tells a reader to run `python -m {module}`, which does not exist"
    )


def test_the_readme_points_at_the_stage_for_the_demo_not_the_disposable_seed() -> None:
    """`agent.seed` mints fresh identifiers per run, so its console URL changes every time.

    Right for exploring, wrong for anything filmed or written into a script. The README said to use
    it for the demonstration for a while after `agent.stage` existed.
    """

    readme = " ".join(_readme().split())

    assert "python -m agent.stage" in readme
    assert "For the demonstration, use `python -m agent.stage`" in readme
    assert "wrong for anything you intend to film" in readme


def test_the_readme_still_separates_authorization_from_payment() -> None:
    """The sentence the whole project turns on, in the file most people read first."""

    readme = " ".join(_readme().split())

    assert "obtains the right to buy something and never obtains the ability to pay" in readme


def test_the_readme_makes_no_claim_the_limitations_page_denies() -> None:
    """Two documents disagreeing about what the project does is worse than either being wrong."""

    readme = _readme().casefold()

    for claim in ("tamper-evident receipt", "receipts are tamper-evident", "production-ready"):
        assert claim not in readme, f"the README claims {claim!r}, which limitations.md denies"
