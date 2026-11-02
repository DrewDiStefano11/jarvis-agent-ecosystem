"""Independent limit enforcement without live requests or fabricated account evidence."""

from datetime import UTC, datetime, timedelta

import pytest


def fixture_state(**limits):
    from app.model_providers.groq_rate_limits import bootstrap_state, complete, parse_headers
    from app.models.groq_capacity import GroqLimits

    now = datetime(2026, 11, 2, 16, tzinfo=UTC)
    policy = GroqLimits(
        rpm=limits.get("rpm", 30),
        rpd=limits.get("rpd", 1000),
        tpm=limits.get("tpm", 8000),
        tpd=limits.get("tpd", 200000),
    )
    state = bootstrap_state(policy, "bootstrap", now, min(100, policy.tpm, policy.tpd))
    headers = parse_headers(
        {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-reset-requests": "1m26.4s",
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "7990",
            "x-ratelimit-reset-tokens": "1s",
        },
        now,
    )
    return complete(state, "bootstrap", now, 10, headers), now


@pytest.mark.parametrize("dimension", ["rpm", "rpd", "tpm", "tpd"])
def test_each_limit_blocks_independently_without_a_provider_request(dimension):
    from app.model_providers.groq_rate_limits import RateBlocked, reserve

    limits = {dimension: 1 if dimension in {"rpm", "rpd"} else 10}
    state, now = fixture_state(**limits)
    with pytest.raises(RateBlocked) as blocked:
        reserve(state, "next", now + timedelta(milliseconds=1), 1)
    assert dimension in blocked.value.dimensions


def test_reservations_refund_only_known_unused_tokens_and_cannot_settle_twice():
    from app.model_providers.groq_rate_limits import complete, reserve

    state, now = fixture_state()
    held = reserve(state, "next", now, 200)
    assert held.remote_tpm_remaining == state.remote_tpm_remaining - 200
    completed_at = now + timedelta(microseconds=1)
    settled = complete(held, "next", completed_at, 50, None)
    assert settled.remote_tpm_remaining == state.remote_tpm_remaining - 50
    assert complete(settled, "next", completed_at, 1, None) == settled


def test_rolling_windows_and_provider_reset_are_separate():
    from app.model_providers.groq_rate_limits import RateBlocked, reserve

    state, now = fixture_state(rpm=1)
    with pytest.raises(RateBlocked):
        reserve(state, "early", now + timedelta(seconds=59), 1)
    assert reserve(state, "ready", now + timedelta(seconds=60), 1)


def test_429_retains_uncertain_reservation_and_blocks_until_retry_after():
    from app.model_providers.groq_rate_limits import RateBlocked, rejected, reserve

    state, now = fixture_state()
    state = reserve(state, "request", now, 200)
    state = rejected(state, "request", now, {"retry-after": "12"})
    assert state.reservations["request"].accounted_tokens == 200
    with pytest.raises(RateBlocked):
        reserve(state, "blocked", now + timedelta(seconds=11), 1)
    assert reserve(state, "later", now + timedelta(seconds=12), 1)


def test_missing_headers_never_initialize_verified_remote_capacity():
    from app.model_providers.groq_rate_limits import RateBlocked, bootstrap_state, complete, reserve
    from app.models.groq_capacity import GroqLimits

    now = datetime(2026, 11, 2, tzinfo=UTC)
    state = complete(
        bootstrap_state(GroqLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000), "only", now, 100),
        "only",
        now,
        5,
        None,
    )
    with pytest.raises(RateBlocked):
        reserve(state, "never", now, 1)


@pytest.mark.parametrize("value", ["garbage", "-1s", "999999999999999d", "1s2s"])
def test_untrusted_reset_values_fail_closed(value):
    from app.model_providers.groq_rate_limits import parse_duration

    with pytest.raises(ValueError):
        parse_duration(value)


def test_missing_response_headers_invalidate_initialized_capacity():
    from app.model_providers.groq_rate_limits import RateBlocked, complete, reserve

    state, now = fixture_state()
    state = complete(reserve(state, "next", now, 200), "next", now, 50, None)
    with pytest.raises(RateBlocked) as blocked:
        reserve(state, "blocked", now, 1)
    assert "provider_capacity_unknown" in blocked.value.dimensions


def test_retry_after_http_date_without_timezone_is_not_verified():
    from app.model_providers.groq_rate_limits import rejected, reserve

    state, now = fixture_state()
    state = rejected(
        reserve(state, "next", now, 200), "next", now, {"retry-after": "Tue, 03 Nov 2026 16:00:00"}
    )
    assert state.unknown_provider_capacity and state.blocked_until is None


def test_older_headers_cannot_clear_a_newer_missing_header_observation():
    from app.model_providers.groq_rate_limits import complete, reserve

    state, now = fixture_state()
    old = state.last_headers
    state = reserve(state, "older", now, 100)
    state = reserve(state, "newer", now, 100)
    state = complete(state, "newer", now + timedelta(seconds=1), 10, None)
    state = complete(state, "older", now + timedelta(seconds=2), 10, old)
    assert state.unknown_provider_capacity


def test_concurrent_429_cannot_shorten_an_existing_reset_deadline():
    from app.model_providers.groq_rate_limits import RateBlocked, rejected, reserve

    state, now = fixture_state()
    state = reserve(reserve(state, "a", now, 100), "b", now, 100)
    state = rejected(state, "a", now, {"retry-after": "60"})
    state = rejected(state, "b", now + timedelta(seconds=1), {"retry-after": "1"})
    with pytest.raises(RateBlocked):
        reserve(state, "too-soon", now + timedelta(seconds=3), 1)
    assert state.blocked_until == now + timedelta(seconds=60)


def test_older_headers_cannot_clear_a_newer_uncertain_observation():
    from app.model_providers.groq_rate_limits import complete, reserve, uncertain

    state, now = fixture_state()
    old = state.last_headers
    state = reserve(reserve(state, "older", now, 100), "timeout", now, 100)
    state = uncertain(state, "timeout", now + timedelta(seconds=1))
    state = complete(state, "older", now + timedelta(seconds=2), 10, old)
    assert state.unknown_provider_capacity


def test_late_token_refund_cannot_exceed_latest_observed_capacity():
    from app.model_providers.groq_rate_limits import complete, reserve

    state, now = fixture_state()
    old = state.last_headers
    state = reserve(reserve(state, "a", now, 1000), "b", now, 1000)
    later = old.model_copy(
        update={
            "received_at": now + timedelta(milliseconds=1),
            "tpm_remaining": 50,
            "tpm_resets_at": now + timedelta(minutes=1),
        }
    )
    state = complete(state, "a", now + timedelta(milliseconds=1), 10, later)
    state = complete(state, "b", now + timedelta(milliseconds=2), 10, old)
    assert state.remote_tpm_remaining <= 50


def test_late_refund_preserves_reservations_made_after_newer_headers():
    from app.model_providers.groq_rate_limits import complete, reserve

    state, now = fixture_state()
    old = state.last_headers
    state = reserve(reserve(state, "older", now, 100), "newer", now, 100)
    snapshot = old.model_copy(
        update={
            "received_at": now + timedelta(milliseconds=1),
            "tpm_remaining": 800,
            "tpm_resets_at": now + timedelta(minutes=1),
        }
    )
    state = complete(state, "newer", now + timedelta(milliseconds=1), 10, snapshot)
    state = reserve(state, "pending", now + timedelta(milliseconds=2), 100)
    before = state.remote_tpm_remaining
    state = complete(state, "older", now + timedelta(milliseconds=3), 10, old)
    assert state.remote_tpm_remaining == before


def test_new_header_clamp_keeps_unsettled_request_reservations():
    from app.model_providers.groq_rate_limits import complete, reserve

    state, now = fixture_state()
    state = reserve(reserve(state, "pending", now, 100), "response", now, 100)
    snapshot = state.last_headers.model_copy(
        update={
            "received_at": now + timedelta(milliseconds=1),
            "tpm_remaining": 800,
            "tpm_resets_at": now + timedelta(minutes=1),
        }
    )
    state = complete(state, "response", now + timedelta(milliseconds=1), 10, snapshot)
    assert state.remote_tpm_remaining <= 700
