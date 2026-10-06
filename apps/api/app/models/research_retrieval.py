"""Bounded transport contracts. These records do not grant network authority."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import DomainError
from app.research.url_policy import normalize_url

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")]


def normalized(value: str) -> str:
    try:
        return normalize_url(value).url
    except DomainError:
        raise ValueError("research URL rejected by destination policy") from None


class RetrievalContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RetrievalLimits(RetrievalContract):
    """Trusted operator bounds, separate from any model-generated request."""

    maximumRedirects: int = Field(default=3, ge=0, le=5)
    maximumResponseBytes: int = Field(default=262144, ge=1, le=1048576)
    maximumHeaderBytes: int = Field(default=16384, ge=1024, le=32768)
    maximumDnsAnswers: int = Field(default=32, ge=1, le=32)
    maximumConnections: int = Field(default=2, ge=1, le=4)
    dnsTimeoutSeconds: float = Field(default=3.0, gt=0, le=10)
    connectTimeoutSeconds: float = Field(default=5.0, gt=0, le=10)
    totalTimeoutSeconds: float = Field(default=20.0, gt=0, le=60)


class RetrievalRequest(RetrievalContract):
    requestId: Identity
    taskId: Identity
    runtimeRunId: Identity
    sourceUrl: str = Field(min_length=1, max_length=4096)

    @field_validator("sourceUrl")
    @classmethod
    def valid_source_url(cls, value: str) -> str:
        # Retain exact input for provenance, but never echo failed values in our
        # own error messages. Callers must also suppress Pydantic input details.
        normalized(value)
        return value


class RedirectRecord(RetrievalContract):
    fromUrl: str = Field(min_length=1, max_length=4096)
    toUrl: str = Field(min_length=1, max_length=4096)
    statusCode: Literal[301, 302, 303, 307, 308]

    @field_validator("fromUrl", "toUrl")
    @classmethod
    def normalized_url(cls, value: str) -> str:
        if normalized(value) != value:
            raise ValueError("redirect URL must be normalized")
        return value


class RetrievedText(RetrievalContract):
    requestId: Identity
    resultId: Identity
    taskId: Identity
    runtimeRunId: Identity
    sourceUrl: str = Field(min_length=1, max_length=4096)
    finalUrl: str = Field(min_length=1, max_length=4096)
    retrievedAt: datetime
    statusCode: Literal[200]
    mediaType: Literal["text/plain", "text/html", "application/xhtml+xml"]
    encoding: Literal["utf-8", "ascii"]
    contentDigest: Digest
    byteCount: int = Field(ge=0, le=1048576)
    artifactId: Identity
    redirects: tuple[RedirectRecord, ...] = Field(default=(), max_length=5)
    # No raw page text in the durable metadata; content lives in a bounded artifact.
    trust: Literal["untrusted_external_content"] = "untrusted_external_content"

    @field_validator("retrievedAt")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            raise ValueError("retrieval timestamp must include a timezone")
        if offset % timedelta(minutes=1) != timedelta(0):
            raise ValueError("retrieval timestamp offset must use whole minutes")
        return value

    @model_validator(mode="after")
    def consistent_chain(self):
        current = normalized(self.sourceUrl)
        if normalized(self.finalUrl) != self.finalUrl:
            raise ValueError("final URL must be normalized")
        seen = {current}
        for hop in self.redirects:
            if hop.fromUrl != current or hop.toUrl in seen:
                raise ValueError("redirect chain is inconsistent or cyclic")
            if current.startswith("https:") and hop.toUrl.startswith("http:"):
                raise ValueError("redirect chain downgrades HTTPS")
            current = hop.toUrl
            seen.add(current)
        if current != self.finalUrl:
            raise ValueError("redirect chain must reach the final URL")
        return self


class RetrievalFailure(RetrievalContract):
    requestId: Identity
    resultId: Identity
    taskId: Identity
    runtimeRunId: Identity
    failedAt: datetime
    code: Literal[
        "RESEARCH_URL_INVALID",
        "RESEARCH_ADDRESS_DENIED",
        "RESEARCH_REDIRECT_LIMIT",
        "RESEARCH_REDIRECT_DENIED",
        "RESEARCH_TIMEOUT",
        "RESEARCH_CANCELLED",
        "RESEARCH_RESPONSE_TOO_LARGE",
        "RESEARCH_HEADERS_TOO_LARGE",
        "RESEARCH_CONTENT_UNSUPPORTED",
        "RESEARCH_RESPONSE_INVALID",
        "RESEARCH_NETWORK_UNAVAILABLE",
        "RESEARCH_AUTHORITY_DENIED",
    ]
    # No remote error message, URL, headers, cookies or provider exception payload.

    @field_validator("failedAt")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if value.tzinfo is None or offset is None:
            raise ValueError("failure timestamp must include a timezone")
        if offset % timedelta(minutes=1) != timedelta(0):
            raise ValueError("failure timestamp offset must use whole minutes")
        return value
