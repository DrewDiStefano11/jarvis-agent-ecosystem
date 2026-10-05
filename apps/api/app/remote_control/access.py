"""Transport credentials identify an operator; existing RBAC decides authority."""

import re
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from typing import Literal

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.agent_runtime.authorization import IdentityRuntimeAuthorizer, RuntimeActorContext
from app.core.errors import DomainError
from app.identity.service import IdentityService

RemoteOperation = Literal["read", "submit", "control", "runtime"]
PERMISSIONS = {
    operation: "remote." + operation for operation in ("read", "submit", "control", "runtime")
}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")


@dataclass(frozen=True)
class RemoteAccessDecision:
    actor: RuntimeActorContext
    permission_key: str
    reason_code: str


class RemoteControlAccess:
    """No token or caller-supplied identity can bypass lifecycle checks or RBAC.

    This core is not wired to network routes yet. The eventual gateway must also
    enforce its route allowlist, TLS termination boundary and mutation audit.
    """

    def __init__(self, identity: IdentityService, actor_id: str, token: SecretStr):
        if not actor_id or actor_id != actor_id.strip():
            raise ValueError("Remote operator requires an exact existing identity ID")
        secret = token.get_secret_value()
        if TOKEN_PATTERN.fullmatch(secret) is None:
            raise ValueError("Remote operator token must be 43–128 base64url characters")
        self.identity = identity
        self.authorizer = IdentityRuntimeAuthorizer(identity)
        self.actor_id = actor_id
        self._token_digest = sha256(secret.encode("ascii")).digest()

    def authenticate(
        self,
        authorization: str | None,
        *,
        secure_transport: bool,
        claimed_actor_id: str | None = None,
    ) -> RuntimeActorContext:
        if not secure_transport:
            raise DomainError("REMOTE_TLS_REQUIRED", "Remote access requires HTTPS.", 403)
        parts = authorization.split(" ") if authorization and len(authorization) <= 135 else []
        if (
            len(parts) != 2
            or parts[0].lower() != "bearer"
            or TOKEN_PATTERN.fullmatch(parts[1]) is None
            or not compare_digest(sha256(parts[1].encode("ascii")).digest(), self._token_digest)
        ):
            raise DomainError(
                "REMOTE_AUTHENTICATION_REQUIRED", "Remote authentication required.", 401
            )
        if claimed_actor_id is not None and claimed_actor_id != self.actor_id:
            raise DomainError("REMOTE_ACTOR_MISMATCH", "Credential and actor must match.", 403)
        return self.authorizer.authenticate(self.actor_id)

    def authorize(
        self, actor: RuntimeActorContext, operation: RemoteOperation
    ) -> RemoteAccessDecision:
        # Repeat lifecycle checks rather than trusting a previously authenticated
        # principal across revocation or suspension between operations.
        live_actor = self.authorizer.authenticate(self.actor_id)
        if actor != live_actor or operation not in PERMISSIONS:
            raise DomainError("REMOTE_PERMISSION_DENIED", "Remote operation denied.", 403)
        permission = PERMISSIONS[operation]
        decision = self.identity.check_permission_resource_access(
            live_actor.actor_id, permission, "administrative_function", "remote_control"
        )
        if not decision.allowed:
            raise DomainError("REMOTE_PERMISSION_DENIED", "Remote operation denied.", 403)
        return RemoteAccessDecision(live_actor, permission, decision.reason_code)

    def authorize_in_session(
        self, actor: RuntimeActorContext, operation: RemoteOperation, session: Session
    ) -> RemoteAccessDecision:
        """Recheck live native policy within the mutation's fenced transaction.

        The repository must acquire its existing write fence before invoking this
        method. Authentication outside a transaction is never a commit permit.
        """
        if actor.actor_id != self.actor_id or operation not in PERMISSIONS:
            raise DomainError("REMOTE_PERMISSION_DENIED", "Remote operation denied.", 403)
        permission = PERMISSIONS[operation]
        decision = self.identity.check_permission_resource_access(
            self.actor_id, permission, "administrative_function", "remote_control", session=session
        )
        if not decision.allowed:
            raise DomainError("REMOTE_PERMISSION_DENIED", "Remote operation denied.", 403)
        return RemoteAccessDecision(actor, permission, decision.reason_code)
