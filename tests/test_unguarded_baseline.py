"""The baseline must stay exploitable, and must stay incapable of touching anything real.

Both halves matter. A demonstration that quietly stopped being vulnerable would keep passing while
making the opposite point on screen, and deliberately vulnerable code living in a payments
repository has to be provably inert.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agent.buyer import PurchaseProposal
from demo.unguarded import (
    DEMO_CATALOG,
    CredulousAdapter,
    InMemoryLedger,
    format_report,
    run_baseline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PACKAGE = REPO_ROOT / "demo"

# Anything that could reach a network, a provider, or a database. The baseline may import none of
# them, which is what makes "it cannot move money" checkable rather than a claim in a docstring.
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {"httpx", "requests", "urllib", "socket", "sqlalchemy", "psycopg", "api", "models", "razorpay"}
)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_baseline_cannot_reach_a_network_a_provider_or_a_database() -> None:
    """Checked against the imports, not against the docstring that claims it.

    The claim "this cannot charge anyone" is only worth something if breaking it fails a test. An
    import of `httpx` or `api.database` here would be the first step of turning a demonstration
    into a payment path, and it stops at this line.
    """

    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(
            _imported_roots(path) & FORBIDDEN_IMPORT_ROOTS
        )
        for path in sorted(DEMO_PACKAGE.rglob("*.py"))
        if "__pycache__" not in path.parts and _imported_roots(path) & FORBIDDEN_IMPORT_ROOTS
    }

    assert not offenders, (
        f"the unguarded baseline gained a way to reach something real: {offenders}"
    )


async def test_the_baseline_is_still_exploitable() -> None:
    """The demonstration has to keep demonstrating.

    If someone hardens this adapter, every other test here still passes and the demo silently
    starts showing two identical refusals. That would be worse than deleting it, because the pitch
    would still claim a contrast the screen no longer shows.
    """

    result = await run_baseline("Buy cloud credits for the robotics club.")

    assert result.unguarded_charge is not None, "the unguarded path refused, so it proves nothing"
    assert result.unguarded_charge.merchant_id == "attacker-controlled-merchant"
    assert result.unguarded_charge.amount_minor == 2_000_000
    assert result.overcharge_minor > 0


async def test_the_guarded_contract_discards_the_money_fields_from_the_same_response() -> None:
    """One model response, two adapters, so the paths cannot have been fed different inputs.

    The discarded fields are the whole argument. TrustGate did not recognise an attack; the
    proposal has no field an amount could be written into.
    """

    result = await run_baseline("Buy cloud credits for the robotics club.")

    assert result.guarded_discarded_fields == ("amount_minor", "merchant_id")
    assert result.guarded_proposal.sku == "CLOUD-STARTER"
    assert result.guarded_proposal.quantity == 1
    assert not hasattr(result.guarded_proposal, "amount_minor")


def test_the_proposal_contract_has_no_money_field_to_fill() -> None:
    """Stated directly, because it is the sentence the demo rests on."""

    assert set(PurchaseProposal.model_fields) == {"sku", "quantity", "purpose"}


async def test_the_two_paths_disagree_about_what_was_bought() -> None:
    """The contrast has to be visible in the numbers, not only in the prose around them."""

    result = await run_baseline("Buy cloud credits for the robotics club.")

    assert result.unguarded_amount_minor != result.catalog_price_minor
    assert result.catalog_price_minor == 39_900
    assert result.unguarded_amount_minor == 2_000_000


def test_an_adapter_given_no_amount_charges_nothing() -> None:
    """A model that stays inside the contract should produce no charge even here.

    Otherwise the baseline would be charging on every run regardless of the injection, and the
    demonstration would be of a broken adapter rather than of a followed instruction.
    """

    ledger = InMemoryLedger()

    charge = CredulousAdapter(ledger).pay({"sku": "CLOUD-STARTER", "quantity": 1})

    assert charge is None
    assert ledger.charges == []


async def test_the_report_names_both_outcomes_without_overstating_either() -> None:
    result = await run_baseline("Buy cloud credits for the robotics club.")

    report = format_report(result)

    assert "attacker-controlled-merchant" in report
    assert "amount_minor, merchant_id" in report
    # The claim the demo is allowed to make, and the one it is not.
    assert "had a field for the money" in report
    assert "blocked" not in report.casefold()


def test_the_demo_catalog_carries_the_injection_it_claims_to() -> None:
    """The poisoned description is the untrusted input; without it nothing is being demonstrated."""

    injected = [item for item in DEMO_CATALOG if "TRUSTGATE_DEMO_INJECTION" in item.description]

    assert injected, "no catalog item carries injected content"
    assert "amount_minor=2000000" in injected[0].description


@pytest.mark.parametrize("sku", [item.sku for item in DEMO_CATALOG])
def test_every_demo_catalog_price_is_a_whole_number_of_minor_units(sku: str) -> None:
    item = next(entry for entry in DEMO_CATALOG if entry.sku == sku)

    assert isinstance(item.price_minor, int)
    assert item.price_minor > 0
