"""The positioning page cites regulators. Those citations must stay labelled and stay honest.

A page that draws parallels to national payments infrastructure is one sentence away from claiming
alignment with it. These checks keep every regulatory item marked with the evidence behind it, and
keep the disclaimers that separate resemblance from conformance.
"""

from __future__ import annotations

from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "docs" / "positioning.md"

# Each regulatory item and the label that must accompany it. The three differ: one has an NPCI
# circular, one has only press reporting, one is a recommendation in a report.
EVIDENCE_LABELS = (
    "**Evidence: primary.**",
    "**Evidence: press reporting only.**",
    "**Evidence: a recommendation in a government report, via press.**",
)


def _page() -> str:
    return " ".join(PAGE.read_text(encoding="utf-8").split())


def test_the_page_exists() -> None:
    assert PAGE.is_file(), "docs/positioning.md is missing"


def test_every_regulatory_item_states_the_evidence_behind_it() -> None:
    """The three items have different standing, and the page must keep saying which is which."""

    page = _page()

    for label in EVIDENCE_LABELS:
        assert " ".join(label.split()) in page, f"the page stopped labelling evidence: {label!r}"


def test_the_unpublished_protocol_is_not_described_as_published() -> None:
    """The UAP had no circular, specification, or press release when this was written.

    Saying otherwise to someone who works in Indian payments is the fastest way to lose them.
    """

    page = _page()

    assert "No NPCI circular, specification, or press release for the UAP was found" in page
    assert "Treat every detail as unconfirmed" in page


def test_the_page_denies_compliance_rather_than_implying_it() -> None:
    page = _page()

    for denial in (
        "context, not a compliance claim",
        "Not compliance with any standard",
        "structural, not conformance",
    ):
        assert denial in page, f"the page stopped saying {denial!r}"


def test_the_page_never_claims_conformance_with_any_of_them() -> None:
    """The failure mode is a verb, not proximity.

    Describing what UPI Circle does is honest and sourced. Saying TrustGate *implements* it is a
    claim about conformance that nothing supports. An earlier version of this check flagged any
    sentence mentioning a regulator, which caught the page's own factual descriptions and would
    have pushed the writing toward vagueness rather than accuracy.
    """

    text = PAGE.read_text(encoding="utf-8")
    bodies = ("NPCI", "CERT-In", "MeitY", "UPI Circle", "UAP", "Unified Agent Protocol")
    conformance = (
        "implements",
        "implementing",
        "complies",
        "compliant",
        "conforms",
        "conformant",
        "certified",
        "aligned with",
        "adheres",
        "meets the",
        "approved by",
    )

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "http" in stripped:
            continue
        if not any(body in stripped for body in bodies):
            continue
        folded = stripped.casefold()
        for verb in conformance:
            if verb in folded:
                # Permitted only where the sentence denies it.
                assert "not " in folded or "no " in folded, (
                    f"the page claims conformance: {stripped!r}"
                )


def test_the_sources_are_listed_and_split_by_kind() -> None:
    page = _page()

    assert "npci.org.in" in page, "the primary NPCI circular is no longer cited"
    assert "Press items are cited as press" in page
    assert "re-check the UAP's status before submission" in page
