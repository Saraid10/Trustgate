from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException, status

from mock_provider.signing import sign_payload
from schemas.domain import ProviderEventSimulation

app = FastAPI(title="MCP Payment Safety Mock Provider")
_EVENT_TYPES = {"payment.authorized", "payment.captured", "payment.failed", "payment.refunded"}


@app.post("/mock-provider/simulate/{event_type}")
async def simulate_provider_event(
    event_type: str, payload: ProviderEventSimulation
) -> dict[str, object]:
    if event_type not in _EVENT_TYPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider event")
    secret = os.getenv("PROVIDER_WEBHOOK_SECRET")
    callback_url = os.getenv("PROVIDER_CALLBACK_URL")
    if not secret or not callback_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="provider not configured"
        )
    raw_body = payload.to_webhook_body(event_type)
    async with httpx.AsyncClient() as client:
        response = await client.post(
            callback_url,
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Provider-Signature": sign_payload(raw_body, secret),
            },
        )
    return {"delivery_status": response.status_code, "event_id": str(payload.event_id)}
