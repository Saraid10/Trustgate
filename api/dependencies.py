from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from models.domain import Tenant


async def require_tenant(
    x_tenant_id: Annotated[UUID, Header()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Tenant:
    """Resolve the sole trusted tenant identity accepted by public routes."""

    tenant = await session.scalar(select(Tenant).where(Tenant.id == x_tenant_id))
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    return tenant
