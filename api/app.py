from fastapi import FastAPI

from api.body_limit import BodySizeLimitMiddleware
from api.routes import (
    approvals,
    catalog_payment_requests,
    checkout_authorities,
    checkout_page,
    console,
    delegations,
    evidence,
    internal_policies,
    payment_requests,
    razorpay,
    webhooks,
)

app = FastAPI(title="MCP Payment Safety Testbed")

# Added as pure ASGI rather than through `add_middleware`, so it wraps the application outermost
# and counts the body before anything else in the stack has touched it. Every route below used to
# read whatever it was sent; the webhook guarded itself and nothing else did.
app.add_middleware(BodySizeLimitMiddleware)
app.include_router(payment_requests.router)
app.include_router(catalog_payment_requests.router)
app.include_router(checkout_authorities.router)
app.include_router(evidence.router)
app.include_router(checkout_page.router)
app.include_router(approvals.router)
app.include_router(delegations.router)
app.include_router(internal_policies.router)
app.include_router(razorpay.router)
app.include_router(webhooks.router)
app.include_router(console.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
