"""Render the published attack matrix and mutation table from their registries.

Both tables are generated rather than written by hand so neither can claim more coverage than the
suite actually provides. `python -m scenarios.report` prints the attack matrix and
`python -m scenarios.report --mutations` prints the mutation table; a test asserts each README
section matches its output exactly.

The mutation table is the one that matters most to keep honest. The attack matrix says which
attacks are covered, and the mutation table says whether those covers would notice if the code
stopped defending. A hand-maintained list of the second kind would drift into a list of invariants
the project used to guard.
"""

from __future__ import annotations

import sys

from scenarios.mutation import MUTATIONS
from scenarios.tier_a import REGISTRY

START_MARKER = "<!-- attack-matrix:start -->"
END_MARKER = "<!-- attack-matrix:end -->"
MUTATION_START_MARKER = "<!-- mutation-table:start -->"
MUTATION_END_MARKER = "<!-- mutation-table:end -->"


def render_matrix() -> str:
    lines = [
        "| ID | Attack | Invariant proven | Tests |",
        "|---|---|---|---|",
    ]
    for scenario in REGISTRY:
        tests = "<br>".join(f"`{name}`" for name in scenario.test_names)
        lines.append(f"| {scenario.id} | {scenario.title} | {scenario.invariant} | {tests} |")
    return "\n".join(lines)


def render_section() -> str:
    return f"{START_MARKER}\n{render_matrix()}\n{END_MARKER}"


def render_mutation_table() -> str:
    lines = [
        "| Mutation | Invariant it removes |",
        "|---|---|",
    ]
    for mutation in MUTATIONS:
        lines.append(f"| `{mutation.name}` | {mutation.invariant} |")
    return "\n".join(lines)


def render_mutation_section() -> str:
    return f"{MUTATION_START_MARKER}\n{render_mutation_table()}\n{MUTATION_END_MARKER}"


def extract_section(
    document: str,
    *,
    start_marker: str = START_MARKER,
    end_marker: str = END_MARKER,
) -> str | None:
    start = document.find(start_marker)
    end = document.find(end_marker)
    if start == -1 or end == -1 or end < start:
        return None
    return document[start : end + len(end_marker)]


def extract_mutation_section(document: str) -> str | None:
    return extract_section(
        document, start_marker=MUTATION_START_MARKER, end_marker=MUTATION_END_MARKER
    )


if __name__ == "__main__":
    if "--mutations" in sys.argv[1:]:
        print(render_mutation_section())
    else:
        print(render_section())
