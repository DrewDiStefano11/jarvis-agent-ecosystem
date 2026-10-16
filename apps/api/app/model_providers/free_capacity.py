from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import FreeQuotaRow
from app.model_providers.errors import ProviderExecutionDisabledError, QuotaExhaustedError
from app.models.ai_capacity import FreeAllowance

PAID_INFERENCE_CEILING_USD = 0


def bucket_key(grant: FreeAllowance) -> str:
    # Models and integration names deliberately do not split an account allowance.
    return sha256(f"{grant.account_id}\0{grant.shared_bucket}".encode()).hexdigest()


def window_key(grant: FreeAllowance) -> str:
    return grant.window_start.astimezone(UTC).isoformat()


def reset_key(grant: FreeAllowance) -> str:
    return grant.resets_at.astimezone(UTC).isoformat()


def require_allowance(grant: FreeAllowance, model: str, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    if not (
        grant.nonbillable is True
        and grant.billing_enabled is False
        and grant.automatic_recharge is False
        and grant.credit_consumption is False
        and grant.verified_at <= current < min(grant.valid_until, grant.resets_at)
        and grant.window_start <= current
        and model in grant.models
    ):
        raise ProviderExecutionDisabledError(
            "no current verified nonbillable allowance for this model",
            provider=grant.provider,
            model=model,
        )


class FreeCapacityRepository:
    """Atomic persistent reservations, including failures and uncertain outcomes.

    A new allowance window requires refreshed operator evidence. Recreating a
    router or changing a model never refills a shared account bucket.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def configure(self, grants: list[FreeAllowance]) -> None:
        groups: dict[str, FreeAllowance] = {}
        for grant in grants:
            key = bucket_key(grant)
            prior = groups.get(key)
            if prior is not None and (
                window_key(prior) != window_key(grant)
                or prior.resets_at != grant.resets_at
                or prior.remaining_requests != grant.remaining_requests
            ):
                raise ValueError("shared account allowances require identical quota evidence")
            groups[key] = grant
        with self.sessions.begin() as session:
            if session.bind is None or session.bind.dialect.name not in {"sqlite", "postgresql"}:
                raise ProviderExecutionDisabledError("atomic free quota storage is unavailable")
            insert = sqlite_insert if session.bind.dialect.name == "sqlite" else postgres_insert
            for key, grant in groups.items():
                session.execute(
                    insert(FreeQuotaRow)
                    .values(
                        bucket_key=key,
                        window_key=window_key(grant),
                        resets_at=reset_key(grant),
                        remaining_requests=grant.remaining_requests,
                        exhausted=False,
                    )
                    .on_conflict_do_nothing(index_elements=["bucket_key"])
                )
                # Clamp in SQL, never from an ORM snapshot that could refund a
                # reservation made concurrently by another process.
                session.execute(
                    update(FreeQuotaRow)
                    .where(
                        FreeQuotaRow.bucket_key == key,
                        FreeQuotaRow.window_key == window_key(grant),
                        FreeQuotaRow.resets_at == reset_key(grant),
                    )
                    .values(
                        remaining_requests=case(
                            (
                                FreeQuotaRow.remaining_requests > grant.remaining_requests,
                                grant.remaining_requests,
                            ),
                            else_=FreeQuotaRow.remaining_requests,
                        )
                    )
                )
                try:
                    require_allowance(grant, sorted(grant.models)[0])
                except ProviderExecutionDisabledError:
                    continue
                # A shifted window label alone cannot refill the allowance.
                session.execute(
                    update(FreeQuotaRow)
                    .where(
                        FreeQuotaRow.bucket_key == key,
                        FreeQuotaRow.window_key < window_key(grant),
                        FreeQuotaRow.resets_at <= window_key(grant),
                    )
                    .values(
                        window_key=window_key(grant),
                        resets_at=reset_key(grant),
                        remaining_requests=grant.remaining_requests,
                        exhausted=False,
                    )
                )

    def available(self, grant: FreeAllowance, model: str) -> bool:
        try:
            require_allowance(grant, model)
        except ProviderExecutionDisabledError:
            return False
        with self.sessions() as session:
            row = session.get(FreeQuotaRow, bucket_key(grant))
            return bool(
                row
                and row.window_key == window_key(grant)
                and row.resets_at == reset_key(grant)
                and not row.exhausted
                and row.remaining_requests > 0
            )

    def reserve(self, grant: FreeAllowance, model: str) -> None:
        require_allowance(grant, model)
        with self.sessions.begin() as session:
            reserved = session.execute(
                update(FreeQuotaRow)
                .where(
                    FreeQuotaRow.bucket_key == bucket_key(grant),
                    FreeQuotaRow.window_key == window_key(grant),
                    FreeQuotaRow.resets_at == reset_key(grant),
                    FreeQuotaRow.exhausted.is_(False),
                    FreeQuotaRow.remaining_requests > 0,
                )
                .values(remaining_requests=FreeQuotaRow.remaining_requests - 1)
            )
            if reserved.rowcount != 1:
                raise QuotaExhaustedError(
                    "verified free allowance exhausted; pause until verified reset",
                    provider=grant.provider,
                    model=model,
                )

    def exhaust(self, grant: FreeAllowance) -> None:
        with self.sessions.begin() as session:
            session.execute(
                update(FreeQuotaRow)
                .where(
                    FreeQuotaRow.bucket_key == bucket_key(grant),
                    FreeQuotaRow.window_key == window_key(grant),
                )
                .values(exhausted=True, remaining_requests=0)
            )

    def snapshot(self) -> list[dict[str, object]]:
        with self.sessions() as session:
            rows = session.scalars(select(FreeQuotaRow).order_by(FreeQuotaRow.bucket_key)).all()
            return [
                {
                    "bucket": row.bucket_key,
                    "window": row.window_key,
                    "remaining_requests": row.remaining_requests,
                    "exhausted": row.exhausted,
                }
                for row in rows
            ]
