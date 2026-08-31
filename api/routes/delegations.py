"""Grant, revoke, and read delegated authority over HTTP.

Granting authority is a human act here, so every write on this router needs the approver token -
the same one the approvals route uses, held by a person and not by any agent.

That is the whole reason this is an HTTP route and not an MCP tool. The agent is offered exactly
five tools and a test asserts it; none of the five is a grant. An agent that can mint its own
authority is the thing this project exists to prevent, and the way to keep that true is to not
build the tool.

The reads are tenant-scoped and show a chain as it stands: who granted it, what it may still spend,
and the human at the top. Nothing here can spend anything.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from delegation.chain import (
    Bounds,
    DelegationRefused,
    grant,
    grant_root,
    resolve_chain,
    revoke,
)
from models.domain import Delegation, SpendingPolicy, Tenant

router = APIRouter(prefix="/api/v1/delegations", tags=["delegations"])


class GrantRequest(BaseModel):
    """What a caller may ask for. Everything money-critical is bounded by the hop above it.

    The validators below guard the two fields whose meaning `min_length` does not actually pin:
    a string of spaces has a length, and a timestamp without a zone has a value.
    """

    delegate_actor_id: str = Field(min_length=1, max_length=255)
    budget_minor: int = Field(gt=0)
    max_amount_minor: int = Field(ge=0)
    allowed_skus: list[str] = Field(min_length=1)
    purpose: str = Field(min_length=1)
    expires_at: datetime
    parent_id: UUID | None = None

    @field_validator("expires_at")
    @classmethod
    def _must_carry_an_offset(cls, value: datetime) -> datetime:
        """A naive datetime is an instant with an opinion nobody wrote down.

        Every timestamp here is stored with a zone and compared against Postgres's `now()`. A naive
        one gets an offset assigned somewhere further down, which makes how long a delegation lives
        depend on where the process happens to be running. That is not a thing anyone should have to
        know in order to read a chain.
        """

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must carry a UTC offset")
        return value

    @field_validator("delegate_actor_id", "purpose")
    @classmethod
    def _must_not_be_only_whitespace(cls, value: str) -> str:
        """`min_length=1` is satisfied by a space, and a space becomes an identity.

        `delegate_actor_id` is what `active_delegation_for` matches a payment against, so a blank
        one is a chain that governs whoever the rest of the system also calls blank.
        """

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("allowed_skus")
    @classmethod
    def _skus_must_be_distinct_and_present(cls, value: list[str]) -> list[str]:
        """Scope is a set, and this is the only field that says what a hop may actually buy.

        A blank entry is scope nothing can satisfy; a repeat is scope stated twice. Neither is
        dangerous on its own, and both mean the caller sent something other than what they meant -
        which matters here because narrowing downstream compares sets, so the list a caller reads
        back would not be the list they sent.
        """

        cleaned = [sku.strip() for sku in value]
        if any(not sku for sku in cleaned):
            raise ValueError("allowed_skus may not contain a blank entry")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("allowed_skus may not repeat a sku")
        return cleaned


class HopResponse(BaseModel):
    delegation_id: UUID
    depth: int
    delegate_actor_id: str
    root_actor_id: str
    budget_minor: int
    allocated_minor: int
    spent_minor: int
    remaining_minor: int
    max_amount_minor: int
    allowed_skus: list[str]
    purpose: str
    expires_at: datetime
    revoked_at: datetime | None


class ChainResponse(BaseModel):
    """A chain root-first, so the human at the top is the first thing read."""

    chain: list[HopResponse]


# The database refuses a widened hop by raising, and a raise reaching FastAPI unhandled is a 500.
# These are refusals, not faults: the caller asked for something the chain does not permit and is
# entitled to know which thing. Anything not listed here is a genuine fault and is left to raise.
_TRIGGER_REFUSALS: tuple[tuple[str, str], ...] = (
    ("DELEGATION_BUDGET_EXHAUSTED", "DELEGATION_BUDGET_EXHAUSTED"),
    ("budget may not exceed its parent", "DELEGATION_BUDGET_EXCEEDS_PARENT"),
    ("per-payment cap may not exceed its parent", "DELEGATION_CAP_EXCEEDS_PARENT"),
    ("may not outlive its parent", "DELEGATION_OUTLIVES_PARENT"),
    ("scope may not widen its parent", "DELEGATION_SCOPE_WIDENS_PARENT"),
    ("only the holder of a delegation", "DELEGATION_NOT_HELD_BY_DELEGATOR"),
    ("revoked delegation cannot be delegated onward", "DELEGATION_PARENT_REVOKED"),
    ("depth must be exactly one below", "DELEGATION_DEPTH_INVALID"),
    ("budget may not exceed the policy daily limit", "DELEGATION_EXCEEDS_POLICY_DAILY_LIMIT"),
    ("per-payment cap may not exceed the policy", "DELEGATION_EXCEEDS_POLICY_PAYMENT_LIMIT"),
    ("may not outlive the policy", "DELEGATION_OUTLIVES_POLICY"),
    ("bounds are fixed at grant", "DELEGATION_BOUNDS_ARE_FIXED"),
    # Not a trigger. `grant` refuses a live holder by name before it inserts, so reaching this is
    # two grants for one actor racing: both looked, both saw room, one lost the index. The reason
    # is the same one the check would have given, because from the caller's side it is the same
    # thing - the actor already holds a delegation.
    ("uq_delegation_one_live_per_actor", "DELEGATION_ACTOR_ALREADY_HOLDS_ONE"),
)


def _as_refusal(error: DBAPIError) -> HTTPException:
    """Turn a trigger's objection into the reason a caller can act on, or re-raise it."""

    message = str(error)
    for fragment, reason in _TRIGGER_REFUSALS:
        if fragment in message:
            return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)
    raise error


def _require_human(token: str) -> str:
    """The approvals route's rule, applied to granting authority rather than to a purchase."""

    expected = os.getenv("DEMO_APPROVER_TOKEN")
    approver_id = os.getenv("DEMO_APPROVER_ID")
    if not expected or not approver_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="approver unavailable"
        )
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="approver token required")
    return approver_id


def _hop(hop: Delegation) -> HopResponse:
    return HopResponse(
        delegation_id=hop.id,
        depth=hop.depth,
        delegate_actor_id=hop.delegate_actor_id,
        root_actor_id=hop.root_actor_id,
        budget_minor=hop.budget_minor,
        allocated_minor=hop.allocated_minor,
        spent_minor=hop.spent_minor,
        remaining_minor=hop.budget_minor - hop.allocated_minor - hop.spent_minor,
        max_amount_minor=hop.max_amount_minor,
        allowed_skus=list(hop.allowed_skus),
        purpose=hop.purpose,
        expires_at=hop.expires_at,
        revoked_at=hop.revoked_at,
    )


@router.post("", response_model=ChainResponse, status_code=status.HTTP_201_CREATED)
async def grant_delegation(
    body: GrantRequest,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    x_approver_token: Annotated[str, Header()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChainResponse:
    """Cut a hop, from a policy if it is a root and from its parent if it is not.

    A child is granted by a human too, for now. The database already refuses a child whose
    delegator does not hold the parent, so the rule that only a holder may delegate onward is
    enforced either way - what is missing is a way to *authenticate* that holder, which is the same
    gap the rest of this API has and is named in `docs/limitations.md`.
    """

    approver_id = _require_human(x_approver_token)
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        bounds = Bounds(
            budget_minor=body.budget_minor,
            max_amount_minor=body.max_amount_minor,
            allowed_skus=tuple(body.allowed_skus),
            purpose=body.purpose,
            expires_at=body.expires_at,
        )
        try:
            if body.parent_id is None:
                policy = await session.scalar(
                    select(SpendingPolicy)
                    .where(SpendingPolicy.tenant_id == tenant.id)
                    .order_by(SpendingPolicy.version.desc())
                )
                if policy is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT, detail="POLICY_NOT_FOUND"
                    )
                held = await grant_root(
                    session,
                    tenant_id=tenant.id,
                    policy=policy,
                    principal_actor_id=approver_id,
                    delegate_actor_id=body.delegate_actor_id,
                    bounds=bounds,
                )
            else:
                parent = await resolve_chain(
                    session, tenant_id=tenant.id, delegation_id=body.parent_id
                )
                held = await grant(
                    session,
                    tenant_id=tenant.id,
                    parent_id=body.parent_id,
                    delegator_actor_id=parent[-1].delegate_actor_id,
                    delegate_actor_id=body.delegate_actor_id,
                    bounds=bounds,
                )
        except DelegationRefused as refused:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=refused.reason
            ) from refused
        except DBAPIError as raised:
            raise _as_refusal(raised) from raised

        chain = await resolve_chain(session, tenant_id=tenant.id, delegation_id=held.id)
        return ChainResponse(chain=[_hop(hop) for hop in chain])


@router.post("/{delegation_id}/revoke", response_model=ChainResponse)
async def revoke_delegation(
    delegation_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    x_approver_token: Annotated[str, Header()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChainResponse:
    """End a hop, and with it every branch below it.

    Descendants are not written to. A hop re-derives its authority from its whole chain on every
    spend, so cutting a link above one is already the end of the branch.
    """

    _require_human(x_approver_token)
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        try:
            await revoke(session, tenant_id=tenant.id, delegation_id=delegation_id)
            chain = await resolve_chain(session, tenant_id=tenant.id, delegation_id=delegation_id)
        except DelegationRefused as refused:
            status_code = (
                status.HTTP_404_NOT_FOUND
                if refused.reason == "DELEGATION_NOT_FOUND"
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(status_code=status_code, detail=refused.reason) from refused
        return ChainResponse(chain=[_hop(hop) for hop in chain])


@router.get("/{delegation_id}", response_model=ChainResponse)
async def read_delegation(
    delegation_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChainResponse:
    """Read a chain. No token, because reading cannot grant or spend anything."""

    try:
        chain = await resolve_chain(session, tenant_id=tenant.id, delegation_id=delegation_id)
    except DelegationRefused as refused:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=refused.reason
        ) from refused
    return ChainResponse(chain=[_hop(hop) for hop in chain])
