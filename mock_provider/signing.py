from __future__ import annotations

import hashlib
import hmac


def sign_payload(raw_body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def signature_is_valid(raw_body: bytes, signature: str | None, secret: str) -> bool:
    return signature is not None and hmac.compare_digest(sign_payload(raw_body, secret), signature)
