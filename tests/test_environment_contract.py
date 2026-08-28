"""Every environment variable the code reads must be documented in `.env.example`.

The submission checklist asks for a clean clone that works. A variable read by the code and absent
from the example file is a clone that fails for a reason nobody wrote down - and the failure lands
on whoever cloned it, not on the person who added the variable.

`AWS_DEFAULT_REGION` was that variable: honoured as a fallback for `AWS_REGION` and mentioned
nowhere, so someone using the standard AWS name would have had it work by accident or not at all.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / ".env.example"

# Packages that ship. Tests set their own variables with monkeypatch and are not a contract.
SHIPPED = ("api", "agent", "demo", "mcp_server", "mock_provider", "policy_engine", "state_machine")

# Read from the process environment rather than configured through `.env`.
NOT_CONFIGURATION = frozenset({"PATH", "HOME", "TEMP", "TMP"})


def _variables_read() -> set[str]:
    found: set[str] = set()
    for package in SHIPPED:
        for path in (REPO_ROOT / package).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            found.update(re.findall(r'getenv\(\s*"([A-Z_][A-Z0-9_]*)"', source))
            found.update(re.findall(r'environ\[\s*"([A-Z_][A-Z0-9_]*)"\s*\]', source))
    return found - NOT_CONFIGURATION


def _variables_documented() -> set[str]:
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", EXAMPLE.read_text(encoding="utf-8"), re.M))


def test_every_variable_the_code_reads_is_in_the_example_file() -> None:
    undocumented = sorted(_variables_read() - _variables_documented())

    assert not undocumented, (
        "these are read by shipped code and absent from .env.example, so a clean clone has no way "
        f"to know they exist: {undocumented}"
    )


# Names that carry a credential. Everything else in the example file is a default that makes a
# clean clone work, and emptying those would defeat the file's purpose.
_SECRET_NAME = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def test_no_credential_in_the_example_file_carries_a_value() -> None:
    """It is committed, so anything with a value in it is published.

    Scoped to names that denote a credential. An earlier version of this required every value to
    be empty, which flagged APP_ENV=local and DATABASE_URL - defaults whose whole job is to make a
    fresh clone start. A rule that fights the file's purpose is the wrong rule.
    """

    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if not any(word in name for word in _SECRET_NAME):
            continue
        assert not value.strip(), (
            f"{name} has a value in the committed example file: {line!r}. "
            "Leave it empty; real values belong in the ignored .env"
        )
