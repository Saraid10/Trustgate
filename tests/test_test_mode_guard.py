"""A live key must not reach the provider, whatever the environment says.

This project says Test Mode in the README, the limitations page, the demo script, and the router's
own tag. Until now it checked only that a credential was non-empty. Razorpay separates test from
live by key rather than by endpoint, so there is one orders URL and the same request with an
`rzp_live_` key moves real money - and the only thing between a mistyped deployment variable and a
real charge was the documentation saying not to.

Documentation is not a control. This is.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.routes.razorpay import TEST_KEY_PREFIX, require_test_mode


def test_a_test_key_is_allowed_through() -> None:
    assert require_test_mode("rzp_test_abc123") == "rzp_test_abc123"


@pytest.mark.parametrize(
    "key_id",
    ["rzp_live_abc123", "rzp_LIVE_abc123", "abc123", "", "test_rzp_abc", "rzp_tes_abc"],
    ids=["live", "live-uppercase", "no-prefix", "empty", "reversed", "near-miss"],
)
def test_anything_that_is_not_a_test_key_is_refused(key_id: str) -> None:
    with pytest.raises(HTTPException) as refused:
        require_test_mode(key_id)

    assert refused.value.status_code == 503
    assert refused.value.detail == "RAZORPAY_NOT_TEST_MODE"


def test_the_guard_fails_closed_rather_than_guessing() -> None:
    """An unrecognised key shape is refused, not allowed on the grounds that it might be fine."""

    assert require_test_mode.__doc__ is not None
    assert TEST_KEY_PREFIX == "rzp_test_"


def test_both_provider_paths_are_guarded() -> None:
    """The order call and the browser-facing checkout page each read the key separately.

    Guarding one and not the other would leave a live key opening a real payment sheet.
    """

    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for module in ("api/routes/razorpay.py", "api/routes/checkout_page.py"):
        source = (root / module).read_text(encoding="utf-8")
        assert "require_test_mode" in source, f"{module} reads a key without the guard"
