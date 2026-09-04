"""Refuse an oversized request body before the application spends anything on it.

Every route but one used to read whatever it was sent. The server buffers a body into memory and
FastAPI hands it to Pydantic before a single validator runs, so a request is fully received and
parsed *before* the tenant header is consulted - which puts the cost on the server and the choice
with the caller. That is the shape of the problem: work done ahead of authorization.

`api/routes/razorpay.py` already guards the webhook, because that is the endpoint a stranger can
reach without credentials in a real deployment. This applies the same reasoning everywhere else.

**Two checks, because one of them is a courtesy.** `Content-Length` is client-supplied: an attacker
can understate it or omit it entirely with chunked encoding. Refusing on it avoids buffering when
the client is honest, and proves nothing when it is not. The measured check is the real boundary -
the body is read in chunks and abandoned the moment it passes the limit, so at most one chunk more
than the cap is ever held.

**What this is not.** It is defence in depth, not the boundary. A deployment behind a proxy should
cap the body there too, where it can be refused before it reaches this process at all. The webhook
keeps its own tighter limit for the same reason a lock has a chain: the general rule is not a
reason to loosen the specific one.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

_DEFAULT_MAX_BODY_BYTES = 256 * 1024
"""Generous for everything this API accepts and small enough to be worth refusing.

The largest legitimate request here is a catalog purchase carrying a purpose string, which is
measured in hundreds of bytes. A quarter of a megabyte leaves several orders of magnitude of room
rather than inviting a caller to discover the ceiling during a demo.
"""

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


def max_body_bytes() -> int:
    """The cap, overridable so a deployment can tighten it without editing code.

    A malformed value is ignored rather than raising. Refusing to start because an environment
    variable is wrong would turn a typo into an outage, and the default is safe.
    """

    configured = os.getenv("TRUSTGATE_MAX_BODY_BYTES")
    if configured and configured.isdigit() and int(configured) > 0:
        return int(configured)
    return _DEFAULT_MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    """Pure ASGI, so the body can be counted as it arrives rather than after it is assembled.

    Written at this level deliberately. Starlette's `BaseHTTPMiddleware` would hand us a `Request`
    whose `body()` reads the whole thing first, which is the cost this exists to avoid - and
    replaying a consumed body to the route underneath is the kind of subtlety that breaks the
    webhook's raw-byte signature check in a way tests would not obviously catch.
    """

    def __init__(self, app: Any, *, max_bytes: int | None = None) -> None:
        self.app = app
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        # Read per-request rather than at construction, so a test can set the environment variable
        # without rebuilding the application.
        return self._max_bytes if self._max_bytes is not None else max_body_bytes()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_bytes
        headers = {key.lower(): value for key, value in scope.get("headers", [])}

        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _refuse(send, limit)
            return

        # Read the body ourselves so it can be abandoned mid-stream. At most one chunk beyond the
        # limit is ever held, which is the difference between a bounded cost and an unbounded one.
        chunks: list[bytes] = []
        received = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk: bytes = message.get("body", b"")
            received += len(chunk)
            if received > limit:
                await _refuse(send, limit)
                return
            if chunk:
                chunks.append(chunk)
            more_body = bool(message.get("more_body", False))

        body = b"".join(chunks)

        # Replay it downstream byte for byte. The webhook verifies its signature over exactly these
        # bytes, so anything that reassembled or re-encoded them here would break it.
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)


async def _refuse(send: Send, limit: int) -> None:
    """A refusal in the vocabulary the rest of the system uses, not a bare status code."""

    payload = json.dumps(
        {"detail": "REQUEST_BODY_TOO_LARGE", "max_bytes": limit}, separators=(",", ":")
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload, "more_body": False})
