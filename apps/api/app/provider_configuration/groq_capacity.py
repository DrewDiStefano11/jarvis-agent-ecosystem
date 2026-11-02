"""Native four-limit accounting; never configuration, activation or dispatch authority.

Callers must retain the normal credential, allowance, RBAC and activation fences.
Only a successful native acceptance can initialize this account/model ledger.
"""

from uuid import uuid4

from app.core.errors import DomainError
from app.model_providers import groq_rate_limits as limits
from app.model_providers.errors import ProviderExecutionDisabledError
from app.model_providers.free_capacity import require_nonbillable_account
from app.provider_configuration.groq_repository import GroqRepository, rate_scope


class GroqCapacityStore:
    def __init__(self, app):
        self.app = app
        self.repository = GroqRepository(app)

    def _load(self, session, grant, model, now, *, require_current=True):
        if grant.integration != "groq" or model not in grant.models:
            raise limits.RateBlocked(["provider_mismatch"])
        row, state = self.repository.rate(session, rate_scope(grant.account_id, model))
        if row is None:
            raise limits.RateBlocked(["acceptance_required"])
        _, receipt = self.repository.acceptance(session, row.acceptance_id)
        if receipt.status != "succeeded" or not receipt.initialized_capacity:
            raise limits.RateBlocked(["acceptance_required"])
        if require_current:
            # Only this native receipt-backed path uses header-tracked quota.
            # Account expiry and every nonbillable safeguard remain mandatory.
            require_nonbillable_account(grant, model, now)
        return row, state

    def available(self, grant, model, token_bound):
        try:
            with self.repository.sessions() as session:
                now = self.repository.now()
                _, state = self._load(session, grant, model, now)
                limits.reserve(state, f"probe-{uuid4().hex}", now, token_bound)
            return True
        except (limits.RateBlocked, ProviderExecutionDisabledError, DomainError):
            return False

    def reserve(self, grant, model, request_id, token_bound):
        with self.app.state.task_leases._write() as session:
            now = self.repository.now()
            row, state = self._load(session, grant, model, now)
            state = limits.reserve(state, request_id, now, token_bound)
            self.repository.write(session, row, state, "rate")
        self.app.state.repository.refresh_event_cursor()
        return request_id

    def complete(
        self, grant, model, request_id, actual_tokens, response_headers, *, received_at=None
    ):
        with self.app.state.task_leases._write() as session:
            now = self.repository.now()
            row, state = self._load(session, grant, model, now, require_current=False)
            observed = received_at or now
            try:
                headers = limits.parse_headers(response_headers, observed)
            except (ValueError, TypeError):
                headers = None
            state = limits.complete(
                state, request_id, now, actual_tokens, headers, observed_at=observed
            )
            self.repository.write(session, row, state, "rate")
        self.app.state.repository.refresh_event_cursor()

    def rejected(self, grant, model, request_id, response_headers):
        with self.app.state.task_leases._write() as session:
            now = self.repository.now()
            row, state = self._load(session, grant, model, now, require_current=False)
            state = limits.rejected(state, request_id, now, response_headers)
            self.repository.write(session, row, state, "rate")
        self.app.state.repository.refresh_event_cursor()

    def uncertain(self, grant, model, request_id):
        with self.app.state.task_leases._write() as session:
            now = self.repository.now()
            row, state = self._load(session, grant, model, now, require_current=False)
            state = limits.uncertain(state, request_id, now)
            self.repository.write(session, row, state, "rate")
        self.app.state.repository.refresh_event_cursor()
