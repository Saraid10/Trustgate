"""Prove the request-scoped session actually commits.

Every other test in this suite receives a session that is already inside an explicit transaction
and asserts within it, so a route's writes are visible to the test whether or not they would ever
reach the database. That structure once hid a defect where the API returned a success response
while the session was closed and every write rolled back.

These tests therefore bypass the shared fixture and drive `get_session` directly against the
database, cleaning up after themselves.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from api.database import SessionLocal, get_session
from models.domain import Tenant


async def _drop_tenant(tenant_id: object) -> None:
    async with SessionLocal() as cleanup:
        await cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await cleanup.commit()


async def test_a_successful_request_commits_its_writes() -> None:
    tenant_id = uuid4()
    name = f"session-lifecycle-{tenant_id}"
    try:
        generator = get_session()
        session = await anext(generator)
        # Mirror a route: read first, which autobegins, then write inside a savepoint.
        await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        async with session.begin_nested():
            session.add(Tenant(id=tenant_id, name=name))
        with pytest.raises(StopAsyncIteration):
            await anext(generator)

        async with SessionLocal() as verifier:
            persisted = await verifier.scalar(select(Tenant).where(Tenant.id == tenant_id))

        assert persisted is not None, "the request-scoped session did not commit its writes"
        assert persisted.name == name
    finally:
        await _drop_tenant(tenant_id)


async def test_a_failed_request_rolls_its_writes_back() -> None:
    tenant_id = uuid4()
    try:
        generator = get_session()
        session = await anext(generator)
        await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        async with session.begin_nested():
            session.add(Tenant(id=tenant_id, name=f"rolled-back-{tenant_id}"))
        with pytest.raises(RuntimeError):
            await generator.athrow(RuntimeError("route failed"))

        async with SessionLocal() as verifier:
            persisted = await verifier.scalar(select(Tenant).where(Tenant.id == tenant_id))

        assert persisted is None, "a failed request left its writes behind"
    finally:
        await _drop_tenant(tenant_id)
