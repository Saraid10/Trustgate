"""Render the published attack matrix from the scenario registry.

The matrix is generated rather than written by hand so it cannot claim more coverage than the
suite actually provides. `python -m scenarios.report` prints it; a test asserts the README section
matches this output exactly.
"""

from __future__ import annotations

from scenarios.tier_a import REGISTRY

START_MARKER = "<!-- attack-matrix:start -->"
END_MARKER = "<!-- attack-matrix:end -->"


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


def extract_section(document: str) -> str | None:
    start = document.find(START_MARKER)
    end = document.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        return None
    return document[start : end + len(END_MARKER)]


if __name__ == "__main__":
    print(render_section())
