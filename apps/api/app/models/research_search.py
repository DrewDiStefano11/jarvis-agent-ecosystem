"""Provider-neutral discovery contracts; search leads are not source evidence."""

import hashlib
import json
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import DomainError
from app.research.url_policy import normalize_url

ProviderKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def search_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class SearchContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SearchBudget(SearchContract):
    maximumQueries: int = Field(default=4, ge=1, le=20)
    maximumResultsPerQuery: int = Field(default=10, ge=1, le=20)
    maximumTotalResults: int = Field(default=40, ge=1, le=100)
    timeoutSeconds: float = Field(default=10.0, gt=0, le=30)
    allowedProviders: tuple[ProviderKey, ...] = Field(min_length=1, max_length=4)
    allowRemote: bool = False

    @model_validator(mode="after")
    def distinct_providers(self):
        if len(set(self.allowedProviders)) != len(self.allowedProviders):
            raise ValueError("search providers must be distinct")
        return self


class SearchQuery(SearchContract):
    query: str = Field(min_length=1, max_length=500)

    @field_validator("query")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 2000:
            raise ValueError("query must contain bounded substantive text")
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("query must not contain control characters")
        return value


class SearchCandidate(SearchContract):
    """Adapter output before destination validation, identity and ranking."""

    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=4096)
    snippet: str | None = Field(default=None, max_length=500)

    @field_validator("title", "snippet")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or any(unicodedata.category(char) == "Cc" for char in value)
        ):
            raise ValueError("discovery text must be substantive and control-free")
        return value


class SearchLead(SearchCandidate):
    query: str = Field(min_length=1, max_length=500)
    rank: int = Field(ge=1, le=20)
    provider: ProviderKey
    retrievedAt: datetime
    resultId: Hash
    resultDigest: Hash
    kind: Literal["discovery_lead"] = "discovery_lead"
    trust: Literal["untrusted_external_content"] = "untrusted_external_content"

    @field_validator("query")
    @classmethod
    def bounded_query(cls, value: str) -> str:
        return SearchQuery.bounded_text(value)

    @field_validator("retrievedAt")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("search timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validated_identity(self):
        try:
            if normalize_url(self.url).url != self.url:
                raise ValueError("lead URL must be normalized")
        except DomainError:
            raise ValueError("lead URL rejected by destination policy") from None
        stable = {"query": self.query, "provider": self.provider, "url": self.url}
        content = stable | {
            "rank": self.rank,
            "title": self.title,
            "snippet": self.snippet,
            "retrievedAt": self.retrievedAt.isoformat(),
        }
        if self.resultId != search_digest(stable) or self.resultDigest != search_digest(content):
            raise ValueError("search lead identity or digest does not match its fields")
        return self


class SearchBatch(SearchContract):
    query: SearchQuery
    provider: ProviderKey
    retrievedAt: datetime
    leads: tuple[SearchLead, ...] = Field(default=(), max_length=20)

    @field_validator("retrievedAt")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return SearchLead.aware_timestamp(value)

    @model_validator(mode="after")
    def consistent_leads(self):
        urls = set()
        for rank, lead in enumerate(self.leads, 1):
            if (
                lead.query != self.query.query
                or lead.provider != self.provider
                or lead.rank != rank
                or lead.retrievedAt != self.retrievedAt
            ):
                raise ValueError("discovery lead does not match its query/provider/rank")
            if lead.url in urls:
                raise ValueError("discovery lead URLs must be distinct")
            urls.add(lead.url)
        return self
