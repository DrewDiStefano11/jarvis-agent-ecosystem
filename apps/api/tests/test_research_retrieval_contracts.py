from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.research_retrieval import (
    RedirectRecord,
    RetrievalFailure,
    RetrievalLimits,
    RetrievalRequest,
    RetrievedText,
)


def result_data():
    return dict(
        requestId="request-1",
        resultId="result-1",
        taskId="task-1",
        runtimeRunId="run-1",
        sourceUrl="https://EXAMPLE.COM:443/a",
        finalUrl="https://example.com/a",
        retrievedAt=datetime.now(UTC),
        statusCode=200,
        mediaType="text/plain",
        encoding="utf-8",
        contentDigest="a" * 64,
        byteCount=10,
        artifactId="artifact-1",
    )


def test_result_is_immutable_bounded_metadata_with_explicit_untrusted_origin():
    result = RetrievedText(**result_data())
    assert result.trust == "untrusted_external_content"
    assert "content" not in result.model_dump()
    assert result.sourceUrl == "https://EXAMPLE.COM:443/a"
    assert RetrievedText.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        result.byteCount = 11
    with pytest.raises(ValidationError):
        RetrievedText(**(result_data() | {"content": "remote page"}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("byteCount", -1),
        ("byteCount", 1048577),
        ("byteCount", "10"),
        ("retrievedAt", datetime(2026, 10, 6)),
        ("contentDigest", "fake"),
        ("finalUrl", "https://EXAMPLE.COM/a"),
        ("sourceUrl", "https://127.0.0.1"),
        ("finalUrl", "https://example.com/b"),
        ("statusCode", 201),
        ("mediaType", "application/octet-stream"),
        ("encoding", "utf-16"),
        ("artifactId", "../secret"),
        ("trust", "trusted_system_policy"),
    ],
)
def test_result_rejects_invalid_or_fabricated_metadata(field, value):
    with pytest.raises(ValidationError):
        RetrievedText(**(result_data() | {field: value}))


def test_chain_requires_continuity_no_cycle_no_downgrade_and_bounded_hops():
    hop = RedirectRecord(
        fromUrl="https://example.com/a", toUrl="https://example.com/b", statusCode=302
    )
    valid = result_data() | {"redirects": (hop,), "finalUrl": hop.toUrl}
    assert RetrievedText(**valid).redirects == (hop,)
    bad = RedirectRecord(fromUrl="https://example.com/c", toUrl=hop.toUrl, statusCode=302)
    for chain in ((bad,), (hop, hop)):
        with pytest.raises(ValidationError):
            RetrievedText(**(valid | {"redirects": chain}))
    cycle = RedirectRecord(fromUrl=hop.toUrl, toUrl=hop.fromUrl, statusCode=301)
    with pytest.raises(ValidationError):
        RetrievedText(**(valid | {"redirects": (hop, cycle), "finalUrl": hop.fromUrl}))
    down = RedirectRecord(fromUrl=hop.fromUrl, toUrl="http://example.com/a", statusCode=302)
    with pytest.raises(ValidationError):
        RetrievedText(**(valid | {"redirects": (down,), "finalUrl": down.toUrl}))
    with pytest.raises(ValidationError):
        RetrievedText(**(valid | {"redirects": (hop,) * 6}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("maximumRedirects", 6),
        ("maximumResponseBytes", 1048577),
        ("maximumHeaderBytes", 32769),
        ("maximumDnsAnswers", 33),
        ("maximumConnections", 5),
        ("dnsTimeoutSeconds", 0.0),
        ("connectTimeoutSeconds", float("nan")),
        ("totalTimeoutSeconds", float("inf")),
        ("maximumRedirects", True),
    ],
)
def test_operator_limits_are_hard_bounded(field, value):
    with pytest.raises(ValidationError):
        RetrievalLimits(**{field: value})


def test_model_request_cannot_supply_authority_or_transport_policy():
    data = dict(
        requestId="request-1",
        taskId="task-1",
        runtimeRunId="run-1",
        sourceUrl="https://example.com",
    )
    assert RetrievalRequest(**data).sourceUrl == data["sourceUrl"]
    for key in ("enabled", "allowPrivate", "headers", "cookies", "limits", "approved"):
        with pytest.raises(ValidationError):
            RetrievalRequest(**(data | {key: True}))


def test_failure_cannot_retain_remote_errors_or_secrets():
    data = dict(
        requestId="request-1",
        resultId="result-1",
        taskId="task-1",
        runtimeRunId="run-1",
        failedAt=datetime.now(UTC),
        code="RESEARCH_TIMEOUT",
    )
    assert RetrievalFailure(**data).code == "RESEARCH_TIMEOUT"
    for key in ("message", "url", "headers", "exception", "cookies"):
        with pytest.raises(ValidationError):
            RetrievalFailure(**(data | {key: "secret"}))
    with pytest.raises(ValidationError):
        RetrievalFailure(**(data | {"failedAt": datetime(2026, 10, 6)}))


def timestamp_record(kind, observed):
    data = result_data()
    if kind == "result":
        return RetrievedText(**(data | {"retrievedAt": observed}))
    return RetrievalFailure(
        **{key: data[key] for key in ("requestId", "resultId", "taskId", "runtimeRunId")},
        failedAt=observed,
        code="RESEARCH_TIMEOUT",
    )


@pytest.mark.parametrize("kind", ["result", "failure"])
@pytest.mark.parametrize("seconds", [30, -30, 0.5])
def test_timestamps_reject_offsets_that_lose_represented_instants(kind, seconds):
    observed = datetime(2026, 10, 6, tzinfo=timezone(timedelta(seconds=seconds)))
    with pytest.raises(ValidationError):
        timestamp_record(kind, observed)


@pytest.mark.parametrize("kind", ["result", "failure"])
@pytest.mark.parametrize("minutes", [0, 330, -210])
def test_minute_offset_timestamps_preserve_instant_after_json_storage(kind, minutes):
    observed = datetime(2026, 10, 6, tzinfo=timezone(timedelta(minutes=minutes)))
    record = timestamp_record(kind, observed)
    restored = type(record).model_validate_json(record.model_dump_json())
    assert restored == record
