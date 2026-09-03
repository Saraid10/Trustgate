"""The demo script names commands and quotes what is on screen. Both can go stale silently.

A script is the one artifact nobody runs until the moment it matters. If a module is renamed or a
phrase in the console changes, the script keeps reading perfectly and stops being true, and the
discovery happens on camera. These checks are cheap and they fail at the moment of the change.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from api.console_view import ConsoleEntry, ConsoleHeadline, render_console
from api.reason_text import humanise

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "demo" / "script.md"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_exists_where_the_build_plan_says_it_does() -> None:
    assert SCRIPT.is_file(), "demo/script.md is missing"


@pytest.mark.parametrize(
    "module",
    sorted(
        set(
            re.findall(
                r"python -m ([a-z_][a-z0-9_.]*)",
                Path("demo/script.md").read_text(encoding="utf-8"),
            )
        )
    ),
)
def test_every_command_the_script_tells_you_to_run_exists(module: str) -> None:
    """Renaming a module should fail here, not halfway through a take."""

    assert importlib.util.find_spec(module) is not None, (
        f"the script runs `{module}`, which is gone"
    )


def test_the_phrases_the_script_points_at_are_still_on_screen() -> None:
    """The script tells the presenter to point at specific words. They have to be there.

    Rendered rather than grepped from the source, because what matters is what a viewer sees.
    """

    from datetime import UTC, datetime
    from uuid import uuid4

    def entry(**overrides: object) -> ConsoleEntry:
        base: dict[str, object] = {
            "payment_request_id": uuid4(),
            "requested_at": datetime.now(UTC),
            "actor_id": "demo-buyer",
            "source": "MCP_AGENT",
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision a build environment.",
            "merchant_display_name": "Campus Cloud",
            "amount_minor": 39_900,
            "currency": "INR",
            "decision": "ALLOW",
            "reasons": (),
            "approval_granted_by": None,
            "delegation_root_actor_id": None,
            "payment_state": "AUTHORIZED",
            "provider_order_id": None,
            "provider_state": None,
        }
        base.update(overrides)
        return ConsoleEntry(**base)  # type: ignore[arg-type]

    # A headline is passed because the script now tells the presenter to read the panel before the
    # table, and a phrase quoted from the panel goes stale exactly as easily as one quoted from a
    # row. Rendered as the refused case, which is the beat that leans on the panel hardest.
    page = render_console(
        tenant_id=uuid4(),
        tenant_name="Demo",
        entries=[
            entry(),
            entry(
                payment_request_id=None, decision="REFUSED", payment_state=None, amount_minor=None
            ),
        ],
        receipt_href="/console/x/requests/{payment_request_id}",
        generated_at=datetime.now(UTC),
        headline=ConsoleHeadline(
            verdict="BLOCKED",
            tone="bad",
            reasons=(humanise("QUANTITY_EXCEEDS_LIMIT"),),
            provider_action_allowed=False,
            provider_action_blocked_reason=humanise("NO_PAYMENT_REQUEST_CREATED"),
            delegation_root_actor_id=None,
            delegation_remaining_minor=None,
            currency="INR",
            has_payment_request=False,
        ),
    )

    quoted_in_the_script = (
        "Not sent to Razorpay yet",
        "no payment request was created",
        "no amount was derived",
        "authorized, not yet paid",
        "Order creation allowed: No",
        "The agent asked for more than the catalog allows",
        "No payment request was created, so there is nothing to write a receipt about.",
    )
    # Whitespace-collapsed and case-folded on the script side only. Prose wraps a quoted phrase
    # across lines and capitalises it at the start of a sentence; neither is the script going
    # stale, and neither should fail a check meant to catch a phrase that actually disappeared.
    script = " ".join(_script().split()).casefold()
    for phrase in quoted_in_the_script:
        assert phrase in page, f"the script quotes {phrase!r}, which the console no longer shows"
        assert phrase.casefold() in script, f"{phrase!r} left the script"


def test_the_script_still_refuses_the_claims_the_project_will_not_make() -> None:
    """The demo is strong because it is exact, and these are the phrasings that would break that.

    Checked against the spoken lines rather than the whole file, because the closing section names
    these words deliberately in order to warn against them.
    """

    spoken = _script().split("## Claims to avoid")[0].casefold()

    # The word itself is allowed and wanted - the script says receipts are "not yet
    # tamper-evident", which is the disclaimer. What is forbidden is asserting the property.
    for claim in ("are tamper-evident", "tamper-evident receipts", "receipts are tamper"):
        assert claim not in spoken, f"the script claims {claim!r}, which is not true of receipts"
    assert "not yet tamper-evident" in spoken, "the receipt limitation stopped being stated"

    for forbidden in ("blocked the attack", "detected the attack", "caught the attack"):
        assert forbidden not in spoken


def test_the_recovery_table_covers_the_failure_that_actually_happened() -> None:
    """Docker stopping mid-session is not hypothetical; it happened while building this."""

    script = _script()

    assert "Docker Desktop stopped" in script
    assert "docker compose up -d" in script


def test_the_spoken_counts_match_what_the_terminal_will_print() -> None:
    """The one mistake in the closing beat a viewer can actually catch.

    Beat 6 runs `make mutation` on screen and then says a number out loud. That number was written
    when the registry held seventeen and was still there at thirty-seven, which would have put the
    narration in direct contradiction with the terminal beside it.

    Spelled out rather than numeric because that is how it is spoken.
    """

    from scenarios.mutation import MUTATIONS
    from scenarios.tier_a import REGISTRY

    spoken = {
        16: "sixteen",
        17: "seventeen",
        18: "eighteen",
        26: "twenty-six",
        33: "thirty-three",
        36: "thirty-six",
        37: "thirty-seven",
        38: "thirty-eight",
        39: "thirty-nine",
        40: "forty",
        41: "forty-one",
        42: "forty-two",
        43: "forty-three",
        44: "forty-four",
        45: "forty-five",
        46: "forty-six",
        47: "forty-seven",
        48: "forty-eight",
        49: "forty-nine",
        50: "fifty",
        51: "fifty-one",
        52: "fifty-two",
        53: "fifty-three",
        54: "fifty-four",
        55: "fifty-five",
    }
    script = SCRIPT.read_text(encoding="utf-8").casefold()

    mutations = spoken.get(len(MUTATIONS))
    scenarios = spoken.get(len(REGISTRY))
    assert mutations is not None, (
        f"the registry holds {len(MUTATIONS)} mutations and this test has no word for it; "
        "add it to `spoken` and update the script"
    )
    assert scenarios is not None, f"no word for {len(REGISTRY)} scenarios"

    assert f"{mutations} deliberate breaks" in script, (
        f"the script does not say '{mutations} deliberate breaks', but `make mutation` will print "
        f"{len(MUTATIONS)}"
    )
    assert f"{scenarios} adversarial scenarios" in script, (
        f"the script does not say '{scenarios} adversarial scenarios', but the registry holds "
        f"{len(REGISTRY)}"
    )
