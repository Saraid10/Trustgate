"""Break each safety invariant on purpose and require the suite to notice.

A passing suite says the code behaves as written. It does not say the tests would object if the
code stopped doing something important. Those are different claims, and only the second is worth
anything when the subject is money.

This applies one deliberate mutation at a time to a safety-critical line, runs the tests that are
supposed to guard it, and records whether they failed. A mutation the suite does not catch is a
guard that exists in the source and not in the verification, which is the more dangerous of the two
places to be missing.

Run with `python -m scenarios.mutation`. It exits non-zero if any mutation survives.

Source files are restored in a `finally` block and the restoration is verified against the working
tree before the report is printed, so an interrupted run cannot leave a mutation behind.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Mutation:
    """One deliberate break, and the tests that must object to it."""

    name: str
    invariant: str
    path: str
    original: str
    mutated: str
    guarding_tests: tuple[str, ...]


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="payment-row-lock",
        invariant="A payment is locked before its state is read and changed.",
        path="models/locking.py",
        original="return statement.with_for_update().execution_options(populate_existing=True)",
        mutated="return statement.execution_options(populate_existing=True)",
        guarding_tests=(
            "tests/test_concurrency.py"
            "::test_a_second_caller_cannot_decide_from_state_the_lock_should_have_hidden",
        ),
    ),
    Mutation(
        name="locked-read-freshness",
        invariant="A locked read decides from the committed row, not from a cached one.",
        path="models/locking.py",
        original="return statement.with_for_update().execution_options(populate_existing=True)",
        mutated="return statement.with_for_update()",
        guarding_tests=(
            "tests/test_concurrency.py"
            "::test_a_second_caller_cannot_decide_from_state_the_lock_should_have_hidden",
        ),
    ),
    Mutation(
        name="locking-discipline",
        invariant="Row locks are taken through the one helper that keeps them meaningful.",
        path="api/routes/webhooks.py",
        original="""            locked(
                select(Payment).where(
                    Payment.id == event.payment_id, Payment.tenant_id == event.tenant_id
                )
            )""",
        mutated="""            select(Payment)
            .where(Payment.id == event.payment_id, Payment.tenant_id == event.tenant_id)
            .with_for_update()""",
        guarding_tests=(
            "tests/test_locking_discipline.py"
            "::test_the_locking_helper_is_the_only_place_a_row_lock_is_taken",
        ),
    ),
    Mutation(
        name="webhook-signature-check",
        invariant="A provider event is authenticated before anything is done with it.",
        path="api/routes/razorpay.py",
        original="    if not signature or not hmac.compare_digest(expected, signature):",
        mutated="    if False:",
        guarding_tests=("tests/test_scenarios_tier_a.py::test_a6_a_forged_signature_is_refused",),
    ),
    Mutation(
        name="webhook-freshness-window",
        invariant="A signed provider event proves origin, not recency.",
        path="api/routes/razorpay.py",
        original='        return "RAZORPAY_WEBHOOK_STALE"',
        mutated="        return None",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py::test_a14_a_stale_signed_event_is_refused",
        ),
    ),
    Mutation(
        name="webhook-timestamp-required",
        invariant="An event that cannot be dated cannot be bounded, so it is refused.",
        path="api/routes/razorpay.py",
        original='        return "RAZORPAY_WEBHOOK_TIMESTAMP_MISSING"',
        mutated="        return None",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py"
            "::test_a14_an_event_with_no_timestamp_is_refused_rather_than_exempted",
        ),
    ),
    Mutation(
        name="approval-expiry",
        invariant="An approval is a permission with a lifetime, not a permanent grant.",
        path="state_machine/transitions.py",
        original="    if approval.expires_at <= now:",
        mutated="    if False:",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py::test_a4_an_expired_approval_cannot_authorize",
        ),
    ),
    Mutation(
        name="authority-policy-drift",
        invariant="An authority does not outlive the policy it was checked against.",
        path="api/routes/checkout_authorities.py",
        original="            or policy.version != authority.policy_version\n",
        mutated="",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py"
            "::test_a13_a_policy_published_after_authorization_revokes_the_authority",
        ),
    ),
    Mutation(
        name="authority-policy-expiry",
        invariant="An authority cannot be spent under a policy that has run out.",
        path="api/routes/checkout_authorities.py",
        original="            or policy.expiry <= datetime.now(UTC)\n",
        mutated="",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py::test_a13_an_expired_policy_cannot_spend_an_authority",
        ),
    ),
    Mutation(
        name="authority-snapshot-binding",
        invariant="An authority is bound to the exact purchase it was issued for.",
        path="api/routes/checkout_authorities.py",
        original=(
            "            or _snapshot_hash(request, authority.policy_version)"
            " != authority.snapshot_hash\n"
        ),
        mutated="",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py"
            "::test_a13_an_amount_edited_after_authorization_breaks_the_snapshot_hash",
        ),
    ),
    Mutation(
        name="daily-budget-predicate",
        invariant="The daily budget upsert refuses to exceed the limit.",
        path="policy_engine/evaluate.py",
        original="""            where=(
                DailySpendReservation.reserved_amount_minor + amount_minor
                <= policy.max_daily_spend_minor
            ),""",
        mutated="",
        guarding_tests=(
            "tests/test_concurrency.py"
            "::test_daily_spend_reservation_race_allows_only_one_final_reservation",
        ),
    ),
    Mutation(
        name="budget-release-from-state-guard",
        invariant="Budget is returned only by a payment that actually reserved it.",
        path="state_machine/transitions.py",
        original=(
            "if to_state in BUDGET_RELEASING_STATES and from_state in RESERVATION_HOLDING_STATES:"
        ),
        mutated="if to_state in BUDGET_RELEASING_STATES:",
        guarding_tests=(
            "tests/test_daily_spend_release.py"
            "::test_a_request_denied_without_reserving_cannot_refund_budget",
        ),
    ),
    Mutation(
        name="checkout-script-escaping",
        invariant="Catalog text cannot terminate the checkout page's script element.",
        path="api/routes/checkout_page.py",
        original="return json.dumps(value).translate(_SCRIPT_ESCAPES)",
        mutated="return json.dumps(value)",
        guarding_tests=(
            "tests/test_checkout_page.py::test_hostile_catalog_text_cannot_escape_the_script_block",
        ),
    ),
    Mutation(
        name="request-session-commit",
        invariant="A successful request commits its writes.",
        path="api/database.py",
        original="""        else:
            await session.commit()""",
        mutated="""        else:
            pass""",
        guarding_tests=(
            "tests/test_session_lifecycle.py::test_a_successful_request_commits_its_writes",
        ),
    ),
    Mutation(
        name="provider-event-identity",
        invariant="Lifecycle events for one payment are distinct events, not replays.",
        path="api/routes/razorpay.py",
        original="provider_event_id=webhook_event_identity(request, event, entity.id),",
        mutated="provider_event_id=entity.id,",
        guarding_tests=(
            "tests/test_razorpay_webhook.py"
            "::test_capture_follows_authorization_for_the_same_payment_id",
        ),
    ),
    Mutation(
        name="self-approval-guard",
        invariant="An approval cannot be granted by the requesting actor.",
        path="api/routes/approvals.py",
        original="        if approver_id == payment_request.actor_id:",
        mutated="        if False:",
        guarding_tests=(
            "tests/test_scenarios_tier_a.py"
            "::test_a5_an_approval_cannot_be_granted_by_the_requesting_actor",
        ),
    ),
    Mutation(
        name="evidence-tenant-filter",
        invariant="Evidence is scoped to the tenant that asked for it.",
        path="api/routes/evidence.py",
        original="""        select(PaymentRequest).where(
            PaymentRequest.id == payment_request_id,
            PaymentRequest.tenant_id == tenant.id,
        )""",
        mutated="""        select(PaymentRequest).where(
            PaymentRequest.id == payment_request_id,
        )""",
        guarding_tests=("tests/test_evidence.py::test_another_tenant_cannot_read_the_receipt",),
    ),
    Mutation(
        name="receipt-search-fail-closed",
        invariant="An incomplete provider search never reports a receipt as absent.",
        path="api/routes/razorpay.py",
        original="""    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail="RAZORPAY_RECEIPT_SEARCH_INCOMPLETE"
    )""",
        mutated="    return matches",
        guarding_tests=(
            "tests/test_provider_order_recovery.py::test_an_incomplete_receipt_search_fails_closed",
        ),
    ),
)


@dataclass
class Result:
    mutation: Mutation
    caught: bool
    detail: str


def _run_guarding_tests(mutation: Mutation) -> tuple[bool, str]:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", "--no-header", *mutation.guarding_tests],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    summary = ""
    for line in reversed(completed.stdout.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            summary = line.strip()
            break
    return completed.returncode != 0, summary or "no pytest summary line"


def _apply(mutation: Mutation) -> Result:
    target = REPO_ROOT / mutation.path
    source = target.read_text(encoding="utf-8")
    if mutation.original not in source:
        return Result(
            mutation,
            caught=False,
            detail="ANCHOR MISSING - the mutation no longer matches the source",
        )
    if source.count(mutation.original) != 1:
        return Result(
            mutation,
            caught=False,
            detail=f"ANCHOR AMBIGUOUS - matched {source.count(mutation.original)} times",
        )
    try:
        target.write_text(source.replace(mutation.original, mutation.mutated), encoding="utf-8")
        caught, detail = _run_guarding_tests(mutation)
        return Result(mutation, caught=caught, detail=detail)
    finally:
        target.write_text(source, encoding="utf-8")


def _working_tree_is_clean() -> bool:
    completed = subprocess.run(  # noqa: S603
        ["git", "diff", "--quiet"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return completed.returncode == 0


def main() -> int:
    dirty_before = not _working_tree_is_clean()
    results = [_apply(mutation) for mutation in MUTATIONS]

    if not dirty_before and not _working_tree_is_clean():
        print("RESTORATION FAILED: the working tree changed. Run `git checkout -- .` before use.")
        return 2

    width = max(len(result.mutation.name) for result in results)
    print(f"\n{len(results)} mutations applied to the safety core\n")
    for result in results:
        mark = "caught  " if result.caught else "SURVIVED"
        print(f"  [{mark}] {result.mutation.name:<{width}}  {result.detail}")
        print(f"{'':>13}{result.mutation.invariant}")

    survivors = [result for result in results if not result.caught]
    print()
    if survivors:
        print(f"{len(survivors)} mutation(s) survived. Those invariants are unguarded:")
        for result in survivors:
            print(f"  - {result.mutation.name}: {result.mutation.invariant}")
        return 1
    print("Every mutation was caught. Each invariant is guarded by a test that fails without it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
