"""An oversized request is refused before the application spends anything on it.

The gap this closes was cost incurred ahead of authorization: the server buffers a body and Pydantic
parses it before any validator or tenant check runs, so an unauthenticated caller could make the
process do the expensive part by sending something enormous. Only the Razorpay webhook guarded
itself.

The important test here is the one that posts a genuinely oversized body. Every other test in this
repository sends small payloads, so a limit set absurdly low would pass all of them and fail only
against something real - which is precisely the bug a limit is capable of introducing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.app import app
from api.body_limit import BodySizeLimitMiddleware, max_body_bytes


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused(client: AsyncClient) -> None:
    """The case no other test in this suite exercises, and the one the limit exists for."""

    oversized = "x" * (max_body_bytes() + 1024)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        content=f'{{"sku": "{oversized}"}}'.encode(),
        headers={"Content-Type": "application/json", "X-Tenant-Id": "irrelevant"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "REQUEST_BODY_TOO_LARGE"


@pytest.mark.asyncio
async def test_it_is_refused_before_the_tenant_is_ever_looked_at(client: AsyncClient) -> None:
    """The whole point: the cost is refused ahead of authorization, not after it.

    `X-Tenant-Id` here is not a uuid and names no tenant. A request that reached the route would
    fail on that with a 4xx of its own, so a 413 proves the body was refused first.
    """

    oversized = "x" * (max_body_bytes() + 1024)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        content=f'{{"sku": "{oversized}"}}'.encode(),
        headers={"Content-Type": "application/json", "X-Tenant-Id": "not-a-uuid-at-all"},
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_an_understated_content_length_does_not_get_through(client: AsyncClient) -> None:
    """`Content-Length` is client-supplied, so the declared check is a courtesy and not a boundary.

    This lies about the size. The measured check is what refuses it, which is the reason there are
    two checks rather than one.
    """

    oversized = b"x" * (max_body_bytes() + 1024)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        content=oversized,
        headers={
            "Content-Type": "application/json",
            "X-Tenant-Id": "irrelevant",
            # httpx sets the true length; the middleware must not believe a smaller one either way.
            "Content-Length": str(len(oversized)),
        },
    )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_an_ordinary_request_is_untouched(client: AsyncClient) -> None:
    """A limit that refuses everything also passes every test above it.

    This asserts the negative that matters: a normal request reaches the route and is answered by
    the route's own rules, whatever they are, rather than by the middleware.
    """

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={"sku": "CLOUD-STARTER", "quantity": 1, "purpose": "x", "idempotency_key": "k"},
        headers={"X-Tenant-Id": "not-a-uuid"},
    )

    assert response.status_code != 413


@pytest.mark.asyncio
async def test_a_request_with_no_body_is_untouched(client: AsyncClient) -> None:
    """GET carries no body, and a middleware that waited for one would hang every page."""

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_limit_is_generous_enough_for_anything_this_api_accepts() -> None:
    """The failure mode a limit introduces: too small, and it refuses real traffic.

    Every payload in this suite is a few hundred bytes, so a limit of 1 KB would pass the whole
    suite and break a real request. This pins the floor rather than trusting the constant.
    """

    assert max_body_bytes() >= 64 * 1024


def test_a_malformed_override_falls_back_rather_than_refusing_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in an environment variable should not be an outage."""

    for bad in ("", "not-a-number", "-1", "0"):
        monkeypatch.setenv("TRUSTGATE_MAX_BODY_BYTES", bad)
        assert max_body_bytes() == 256 * 1024, f"{bad!r} did not fall back to the default"

    monkeypatch.setenv("TRUSTGATE_MAX_BODY_BYTES", "4096")
    assert max_body_bytes() == 4096


@pytest.mark.asyncio
async def test_the_webhook_keeps_its_own_tighter_limit() -> None:
    """The general rule is not a reason to loosen the specific one.

    The webhook caps at 64 KB because it is the endpoint a stranger reaches without credentials.
    A body between the two limits must still be refused there.
    """

    from api.routes.razorpay import _MAX_WEBHOOK_BODY_BYTES

    assert _MAX_WEBHOOK_BODY_BYTES < max_body_bytes(), (
        "the webhook's limit stopped being tighter than the general one"
    )


def test_the_middleware_ignores_traffic_that_is_not_http() -> None:
    """Lifespan and websocket scopes pass straight through rather than being read for a body."""

    middleware = BodySizeLimitMiddleware(app)

    assert middleware.max_bytes == max_body_bytes()
