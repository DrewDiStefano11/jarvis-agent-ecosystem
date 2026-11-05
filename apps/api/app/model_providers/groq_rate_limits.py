"""Pure four-dimensional accounting. Only the native repository grants dispatch authority."""

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from app.models.groq_capacity import (
    GroqHeaderSnapshot,
    GroqLimits,
    GroqRateState,
    GroqReservation,
)


class RateBlocked(Exception):
    def __init__(self, dimensions, retry_at=None):
        self.dimensions = tuple(dimensions)
        self.retry_at = retry_at
        super().__init__("Groq capacity is unavailable")


def parse_duration(value: str) -> float:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("invalid provider reset")
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", value)
    if not parts or "".join(number + unit for number, unit in parts) != value:
        raise ValueError("invalid provider reset")
    units = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
    if len({unit for _, unit in parts}) != len(parts):
        raise ValueError("duplicate reset units")
    seconds = sum(float(number) * units[unit] for number, unit in parts)
    if not 0 <= seconds <= 604800:
        raise ValueError("provider reset exceeds bound")
    return seconds


def parse_headers(headers, received_at: datetime) -> GroqHeaderSnapshot:
    headers = {key.lower(): value for key, value in headers.items()}

    def integer(key):
        value = headers[key]
        if not isinstance(value, str) or not re.fullmatch(r"\d{1,10}", value):
            raise ValueError("invalid provider quota")
        return int(value)

    try:
        return GroqHeaderSnapshot(
            received_at=received_at,
            rpd_limit=integer("x-ratelimit-limit-requests"),
            rpd_remaining=integer("x-ratelimit-remaining-requests"),
            rpd_resets_at=received_at
            + timedelta(seconds=parse_duration(headers["x-ratelimit-reset-requests"])),
            tpm_limit=integer("x-ratelimit-limit-tokens"),
            tpm_remaining=integer("x-ratelimit-remaining-tokens"),
            tpm_resets_at=received_at
            + timedelta(seconds=parse_duration(headers["x-ratelimit-reset-tokens"])),
        )
    except (KeyError, OverflowError):
        raise ValueError("provider quota headers missing or invalid") from None


def _at(state, now):
    if now.tzinfo is None or now < state.last_seen:
        raise RateBlocked(["clock"])
    pending = [item for item in state.reservations.values() if item.status == "reserved"]
    changes = {
        "last_seen": now,
        "reservations": {
            key: value
            for key, value in state.reservations.items()
            if value.created_at > now - timedelta(days=1) or value.status == "reserved"
        },
    }
    if state.remote_rpd_reset is not None and now >= state.remote_rpd_reset:
        changes.update(
            remote_rpd_remaining=max(0, state.remote_rpd_limit - len(pending)),
            remote_rpd_reset=None,
        )
    if state.remote_tpm_reset is not None and now >= state.remote_tpm_reset:
        changes.update(
            remote_tpm_remaining=max(
                0, state.remote_tpm_limit - sum(item.accounted_tokens for item in pending)
            ),
            remote_tpm_reset=None,
        )
    if state.blocked_until is not None and now >= state.blocked_until:
        changes["blocked_until"] = None
    return state.model_copy(update=changes)


def bootstrap_state(limits: GroqLimits, request_id: str, now: datetime, token_bound: int):
    if now.tzinfo is None or not request_id or not 1 <= token_bound <= min(limits.tpm, limits.tpd):
        raise ValueError("invalid bounded bootstrap reservation")
    # This is data only, never a dispatch permit. Native approval must already be
    # consumed transactionally by the caller before this state can be persisted.
    reservation = GroqReservation(
        created_at=now, reserved_tokens=token_bound, accounted_tokens=token_bound
    )
    return GroqRateState(limits=limits, last_seen=now, reservations={request_id: reservation})


def usage(state, now):
    minute = [
        item
        for item in state.reservations.values()
        if item.created_at > now - timedelta(seconds=60)
    ]
    day = [
        item for item in state.reservations.values() if item.created_at > now - timedelta(days=1)
    ]
    return {
        "rpm": len(minute),
        "rpd": len(day),
        "tpm": sum(item.accounted_tokens for item in minute),
        "tpd": sum(item.accounted_tokens for item in day),
    }


def reserve(state, request_id, now, token_bound):
    state = _at(state, now)
    if request_id in state.reservations:
        raise RateBlocked(["duplicate_dispatch"])
    if state.unknown_provider_capacity:
        raise RateBlocked(["provider_capacity_unknown"])
    if state.blocked_until is not None:
        raise RateBlocked(["provider_429"], state.blocked_until)
    if not isinstance(token_bound, int) or isinstance(token_bound, bool) or token_bound < 1:
        raise ValueError("a conservative token bound is required")
    current = usage(state, now)
    blocked = [
        dimension
        for dimension in ("rpm", "rpd", "tpm", "tpd")
        if current[dimension] + (1 if dimension in {"rpm", "rpd"} else token_bound)
        > getattr(state.limits, dimension)
    ]
    if state.remote_rpd_remaining is None or state.remote_rpd_remaining < 1:
        blocked.append("provider_rpd")
    if state.remote_tpm_remaining is None or state.remote_tpm_remaining < token_bound:
        blocked.append("provider_tpm")
    if blocked:
        raise RateBlocked(blocked)
    reservations = dict(state.reservations)
    reservations[request_id] = GroqReservation(
        created_at=now, reserved_tokens=token_bound, accounted_tokens=token_bound
    )
    return state.model_copy(
        update={
            "reservations": reservations,
            "remote_rpd_remaining": state.remote_rpd_remaining - 1,
            "remote_tpm_remaining": state.remote_tpm_remaining - token_bound,
        }
    )


def complete(
    state, request_id, now, actual_tokens, headers: GroqHeaderSnapshot | None, *, observed_at=None
):
    state = _at(state, now)
    reservation = state.reservations[request_id]
    if reservation.status != "reserved":
        return state
    observation = headers.received_at if headers is not None else (observed_at or now)
    if observation.tzinfo is None or observation > now:
        raise ValueError("invalid provider observation time")
    latest = state.last_response_at is None or observation >= state.last_response_at
    valid = (
        isinstance(actual_tokens, int)
        and not isinstance(actual_tokens, bool)
        and 0 <= actual_tokens <= reservation.reserved_tokens
    )
    if not valid:
        return state.model_copy(
            update={
                "unknown_provider_capacity": True,
                "last_response_at": max(state.last_response_at or observation, observation),
            }
        )
    reservations = dict(state.reservations)
    reservations[request_id] = reservation.model_copy(
        update={
            "status": "completed",
            "actual_tokens": actual_tokens,
            "accounted_tokens": actual_tokens,
        }
    )
    changes = {
        "reservations": reservations,
        "unknown_provider_capacity": (headers is None)
        if latest
        else state.unknown_provider_capacity,
        "last_response_at": max(state.last_response_at or observation, observation),
    }
    if state.remote_tpm_remaining is not None and (
        state.last_response_at is None or observation > state.last_response_at
    ):
        changes["remote_tpm_remaining"] = min(
            state.remote_tpm_limit,
            state.last_headers.tpm_remaining
            if state.last_headers is not None and now < state.last_headers.tpm_resets_at
            else state.remote_tpm_limit,
            state.remote_tpm_remaining + reservation.reserved_tokens - actual_tokens,
        )
    if headers is not None:
        if headers.received_at > now:
            raise ValueError("future provider quota observation")
        # Never turn an older response into a newer reset/refill observation.
        if latest and (
            state.last_headers is None or headers.received_at >= state.last_headers.received_at
        ):
            changes.update(
                last_headers=headers,
                unknown_provider_capacity=False
                if state.last_response_at is None or observation > state.last_response_at
                else state.unknown_provider_capacity,
                remote_rpd_limit=min(state.limits.rpd, headers.rpd_limit),
                remote_tpm_limit=min(state.limits.tpm, headers.tpm_limit),
                remote_rpd_reset=headers.rpd_resets_at,
                remote_tpm_reset=headers.tpm_resets_at,
            )
            pending = [item for item in reservations.values() if item.status == "reserved"]
            changes["remote_rpd_remaining"] = min(
                max(0, headers.rpd_remaining - len(pending)),
                state.limits.rpd,
                state.remote_rpd_remaining
                if state.remote_rpd_remaining is not None
                else headers.rpd_remaining,
            )
            changes["remote_tpm_remaining"] = min(
                max(0, headers.tpm_remaining - sum(item.accounted_tokens for item in pending)),
                state.limits.tpm,
                changes.get("remote_tpm_remaining", state.remote_tpm_remaining)
                if state.remote_tpm_remaining is not None
                else headers.tpm_remaining,
            )
    return state.model_copy(update=changes)


def rejected(state, request_id, now, headers):
    state = _at(state, now)
    reservation = state.reservations[request_id]
    if reservation.status != "reserved":
        return state
    reservations = dict(state.reservations)
    reservations[request_id] = reservation.model_copy(update={"status": "rejected"})
    normalized = {key.lower(): value for key, value in headers.items()}
    retry_at = None
    value = normalized.get("retry-after", "")
    try:
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            seconds = float(value)
            if seconds > 604800:
                raise ValueError
            retry_at = now + timedelta(seconds=seconds)
        elif value:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                raise ValueError
            retry_at = retry_at.astimezone(UTC)
            if not now <= retry_at <= now + timedelta(days=7):
                raise ValueError
    except (TypeError, ValueError, OverflowError):
        retry_at = None
    return state.model_copy(
        update={
            "reservations": reservations,
            "blocked_until": max(
                value for value in (state.blocked_until, retry_at) if value is not None
            )
            if state.blocked_until is not None or retry_at is not None
            else None,
            "last_response_at": max(state.last_response_at or now, now),
            "unknown_provider_capacity": state.unknown_provider_capacity or retry_at is None,
        }
    )


def uncertain(state, request_id, now):
    state = _at(state, now)
    reservation = state.reservations[request_id]
    if reservation.status != "reserved":
        return state
    reservations = dict(state.reservations)
    reservations[request_id] = reservation.model_copy(update={"status": "uncertain"})
    return state.model_copy(
        update={
            "reservations": reservations,
            "unknown_provider_capacity": True,
            "last_response_at": max(state.last_response_at or now, now),
        }
    )
