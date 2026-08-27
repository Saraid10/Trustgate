"""The limitations page states numbers and denies properties. Both can go quietly false.

A page whose whole purpose is honesty is the worst place for a stale figure. These checks tie its
counts to the registries they describe, and assert that the disclaimers it exists to make are still
being made.
"""

from __future__ import annotations

import re
from pathlib import Path

from api.routes.razorpay import _DEFAULT_WEBHOOK_MAX_AGE_SECONDS, _MAX_WEBHOOK_BODY_BYTES
from scenarios.mutation import MUTATIONS
from scenarios.tier_a import REGISTRY

PAGE = Path(__file__).resolve().parent.parent / "docs" / "limitations.md"


def _page() -> str:
    return " ".join(PAGE.read_text(encoding="utf-8").split())


def test_the_page_exists_where_everything_else_points_at_it() -> None:
    """`demo/script.md` tells the presenter to say this file names every cut."""

    assert PAGE.is_file(), "docs/limitations.md is referenced by the demo script and is missing"


def test_the_counts_match_the_registries_they_describe() -> None:
    page = _page()

    assert f"{len(REGISTRY)} Tier A adversarial scenarios" in page, (
        f"the page's scenario count is stale; the registry holds {len(REGISTRY)}"
    )
    assert f"{len(MUTATIONS)} mutations" in page, (
        f"the page's mutation count is stale; the suite holds {len(MUTATIONS)}"
    )


def test_the_provider_limits_match_the_code() -> None:
    page = _page()
    hours = _DEFAULT_WEBHOOK_MAX_AGE_SECONDS // 3600
    kilobytes = _MAX_WEBHOOK_BODY_BYTES // 1024

    assert f"default {hours} hours" in page, f"the stated freshness window is not {hours} hours"
    assert f"{kilobytes} KB" in page, f"the stated webhook body cap is not {kilobytes} KB"


def test_the_page_still_denies_the_properties_it_exists_to_deny() -> None:
    """The disclaimers are the point. Losing one is how a limitations page becomes a brochure."""

    page = _page()

    for denial in (
        "not authentication",
        "traceable, not tamper-evident",
        "Test Mode only",
        "No rate limiting anywhere",
        "not a payment processor",
    ):
        assert denial.casefold() in page.casefold(), f"the page stopped saying {denial!r}"


def test_the_page_makes_no_compliance_claim() -> None:
    """Named standards may appear only in the sentence disclaiming them."""

    text = PAGE.read_text(encoding="utf-8")

    for standard in ("PCI DSS", "RBI", "NPCI", "SOC 2"):
        for line in text.splitlines():
            if standard in line:
                assert re.search(r"\bno\b|\bnot\b", line, re.IGNORECASE), (
                    f"{standard!r} appears in a line that does not disclaim it: {line.strip()!r}"
                )


def test_every_deferred_item_says_it_was_deferred() -> None:
    """A stretch item quietly dropped is the failure this section prevents."""

    page = _page()

    for item in ("Branded Whisper", "hash-chained evidence", "Signed mandates", "Risk-signal seam"):
        assert item.casefold() in page.casefold(), f"{item!r} left the deferred list"
    assert "Deferred" in page
