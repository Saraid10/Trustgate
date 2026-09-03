"""A guided tour for someone who has four minutes and a folder they have never seen.

`make verify` answers "does this still hold" and is written for whoever maintains the project.
This answers a different question - "is any of this true" - for someone deciding whether to keep
reading. Those want opposite things: a gate should be exhaustive and silent, a tour should be
short and say what it is showing.

Ordered by what survives a hostile reading. The first step needs no database, no Docker, and no
credentials, because the strongest thing here is a failure rather than a feature: this repository
contains code that pays an attacker, and it runs on a laptop with nothing installed. A reader who
stops after thirty seconds should still have seen that.

Run with `python -m scenarios.triage`, or `make triage`.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = REPO_ROOT / "docs" / "evidence"

# ASCII on purpose. A box-drawing character crashes this tour outright on a Windows console
# running cp1252 - the machine it was written on, and plausibly a reader's too. A tour that raises
# UnicodeEncodeError before printing anything is worse than a plain-looking one, and this is the
# second time that encoding has bitten this repository.
_RULE = "-" * 78


def _heading(step: str, title: str, needs: str) -> None:
    print(f"\n{_RULE}\n  {step}  {title}\n  {' ' * len(step)}  {needs}\n{_RULE}")


def _quote(lines: list[str]) -> None:
    for line in lines:
        print(f"      {line}")


def _postgres_is_up() -> bool:
    """Ask the database rather than assuming, so the tour degrades instead of erroring.

    A judge who has not started Docker should still get the first and last steps, and be told
    exactly what the middle one needs - not handed a stack trace from psycopg.
    """

    try:
        from sqlalchemy import create_engine, text

        from api.database import DATABASE_URL

        engine = create_engine(DATABASE_URL.replace("+psycopg", "+psycopg"), pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return False
    return True


def _the_problem() -> None:
    _heading("1/3", "The problem, in this repository's own code", "no database, no Docker, no keys")
    print(
        "\n  One model response, handed to two payment adapters. The catalog description was\n"
        "  written by a supplier and contains an instruction. The agent follows it.\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "demo.unguarded"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    tail = [line for line in completed.stdout.splitlines() if line.strip()][-14:]
    _quote(tail)
    print(
        "\n  Nothing detected an attack. One interface had a field for the amount and the\n"
        "  other did not. That is the whole difference, and it is why this is an\n"
        "  authorization problem rather than a detection problem.\n"
    )


def _the_evidence() -> None:
    _heading("2/3", "What actually happened against the real provider", "reads files, runs nothing")
    artifact = EVIDENCE / "m3-provider-delivered-webhook.json"
    if not artifact.is_file():
        print("\n  (missing - expected docs/evidence/m3-provider-delivered-webhook.json)\n")
        return
    record = json.loads(artifact.read_text(encoding="utf-8"))
    payment = record.get("payment") or {}
    order = record.get("provider_order") or {}
    events = [event["event_type"] for event in record.get("provider_events") or []]
    trail = [entry["event_kind"] for entry in record.get("audit_trail") or []]

    print()
    _quote(
        [
            f"provider order   {order.get('razorpay_order_id')}   (real Razorpay Test Mode)",
            f"payment state    {payment.get('state')}",
            f"captured         INR {(payment.get('captured_amount_minor') or 0) / 100:,.2f}",
            f"provider events  {' -> '.join(events) if events else '(none)'}",
        ]
    )
    # The ordering is the argument, so it is printed rather than described. A reader can see the
    # callback arriving after the capture, which is the whole reason the callback is distrusted.
    if trail:
        print("\n      audit trail:")
        for kind in trail:
            print(f"        {kind}")
    print(
        "\n  Razorpay created the order, a human paid it, and Razorpay's own signed event moved\n"
        "  the money. The browser callback is verified and deliberately changes nothing - it\n"
        "  passes through the buyer's machine, so it is not capture evidence.\n"
        "\n  Provenance for both evidence artifacts is in docs/evidence/README.md, including how\n"
        "  to tell a provider-delivered event from a locally signed one using stored data.\n"
    )


def _the_proof(postgres: bool) -> None:
    _heading("3/3", "Why the guards can be believed", "needs Postgres, takes a few minutes")
    if not postgres:
        print(
            "\n  Skipped - Postgres is not reachable. Start it and re-run:\n"
            "\n      docker compose up -d\n"
            "      python -m scenarios.triage\n"
            "\n  What it does: deletes each safety guard on purpose, one at a time, and requires\n"
            "  the tests that protect it to fail. A passing suite says the code behaves as\n"
            "  written; it does not say the tests would object if it stopped. Only the second\n"
            "  claim is worth anything when the subject is money.\n"
            "\n  That method found a policy-expiry check this project had shipped unguarded:\n"
            "  302 tests passed with it deleted, after two clean human audits.\n"
        )
        return

    from scenarios.mutation import MUTATIONS

    print(
        f"\n  Deleting {len(MUTATIONS)} safety guards, one at a time, and requiring the tests\n"
        "  that protect each one to fail.\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "scenarios.mutation"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    survivors = [line for line in completed.stdout.splitlines() if "SURVIVED" in line]
    tail = [line for line in completed.stdout.splitlines() if line.strip()][-2:]
    _quote(tail)
    if survivors:
        print("\n      unguarded invariants:")
        _quote(survivors)
    print(
        "\n  This is how a policy-expiry check that 302 passing tests missed was found, after\n"
        "  two clean human audits.\n"
    )


def main() -> None:
    # Line-buffered on purpose. Python block-buffers stdout when it is not a terminal, so a reader
    # who pipes this to `tee` or a log sees nothing at all until the mutation step finishes minutes
    # later - which reads as a hang on the one command written to make a good first impression.
    #
    # Guarded rather than ignored: `sys.stdout` is only a TextIOWrapper when it is a real stream,
    # and under pytest's capture it is not. Asking anyway would raise inside a test that imports
    # this module for any other reason.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    print(
        "\n  TrustGate - a guided tour\n"
        "  An AI agent can ask to buy something. It can never decide what that costs,\n"
        "  who gets paid, or whether the money moves.\n"
    )
    postgres = _postgres_is_up()
    _the_problem()
    _the_evidence()
    _the_proof(postgres)
    print(
        f"{_RULE}\n"
        "  Where to go next\n"
        "    JUDGE.md                every claim, and the command that proves it\n"
        "    README.md               what it is and why the shape is what it is\n"
        "    docs/limitations.md     every deliberate cut, including the unflattering ones\n"
        "    make verify             the full gate: lint, types, migrations, tests, mutations\n"
        f"{_RULE}\n"
    )


if __name__ == "__main__":
    main()
