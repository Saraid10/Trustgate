from fastapi import FastAPI

from api.routes import (
    approvals,
    catalog_payment_requests,
    checkout_authorities,
    internal_policies,
    payment_requests,
    razorpay,
    webhooks,
)

app = FastAPI(title="MCP Payment Safety Testbed")
app.include_router(payment_requests.router)
app.include_router(catalog_payment_requests.router)
app.include_router(checkout_authorities.router)
app.include_router(approvals.router)
app.include_router(internal_policies.router)
app.include_router(razorpay.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
