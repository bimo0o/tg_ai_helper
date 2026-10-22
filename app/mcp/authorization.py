"""Short-lived, argument-bound write authorization in protocol metadata, never model args."""

import hashlib
import hmac
import json
import time
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr

from app.mcp.contracts import Contract

META_KEY = "tg-assistant/authorization"


class AuthorizationError(ValueError):
    pass


class Grant(Contract):
    chat_id: StrictInt = Field(gt=0)
    user_id: StrictInt = Field(gt=0)
    action_id: UUID
    expires_at: StrictInt
    args_hash: StrictStr
    signature: StrictStr


def _encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(arguments: dict) -> str:
    return hashlib.sha256(_encoded({"tool": "add_reminder", "arguments": arguments})).hexdigest()


class WriteAuthority:
    def __init__(self, secret: bytes, *, clock=time.time):
        if len(secret) < 32:
            raise ValueError("MCP signing key must contain at least 32 bytes")
        self.secret = secret
        self.clock = clock

    def sign(self, owner: tuple[int, int], action_id: UUID, arguments: dict) -> dict:
        payload = {
            "chat_id": owner[0],
            "user_id": owner[1],
            "action_id": str(action_id),
            "expires_at": int(self.clock()) + 60,
            "args_hash": _digest(arguments),
        }
        signature = hmac.new(self.secret, _encoded(payload), hashlib.sha256).hexdigest()
        return {META_KEY: {**payload, "signature": signature}}

    def verify(self, metadata, arguments: dict) -> Grant:
        try:
            grant = Grant.model_validate((metadata or {})[META_KEY])
            payload = grant.model_dump(mode="json", exclude={"signature"})
            expected = hmac.new(self.secret, _encoded(payload), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, grant.signature):
                raise ValueError
            if not self.clock() < grant.expires_at <= self.clock() + 60:
                raise ValueError
            if not hmac.compare_digest(_digest(arguments), grant.args_hash):
                raise ValueError
            return grant
        except (KeyError, TypeError, ValueError):
            raise AuthorizationError("Write authorization denied") from None
