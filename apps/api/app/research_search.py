"""Pure discovery normalization and an adapter protocol; no providers activated."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal, Protocol

from app.core.errors import DomainError
from app.models.research_search import (
    SearchBatch,
    SearchBudget,
    SearchCandidate,
    SearchLead,
    SearchQuery,
    search_digest,
)
from app.research.url_policy import normalize_url


class SearchProvider(Protocol):
    """Credentials stay inside an operator-configured adapter, outside records."""

    key: str
    mode: Literal["local", "remote"]

    async def search(
        self, query: SearchQuery, *, maximum_results: int
    ) -> Sequence[SearchCandidate]: ...


def normalize_candidates(
    query: SearchQuery,
    provider: str,
    candidates: Sequence[SearchCandidate],
    *,
    retrieved_at: datetime,
    maximum_results: int,
) -> SearchBatch:
    # Never materialize an unbounded iterable. Concrete adapters must bound bytes,
    # parsing and credentials before constructing their bounded candidate sequence.
    if (
        not isinstance(candidates, (tuple, list))
        or len(candidates) > 20
        or type(maximum_results) is not int
        or not 1 <= maximum_results <= 20
    ):
        raise DomainError(
            "RESEARCH_SEARCH_RESULT_INVALID", "Search results exceed contract bounds.", 422
        )
    urls = set()
    leads = []
    for candidate in candidates:
        if type(candidate) is not SearchCandidate:
            raise DomainError(
                "RESEARCH_SEARCH_RESULT_INVALID",
                "Search adapter returned an invalid candidate.",
                422,
            )
        url = normalize_url(candidate.url).url
        if url in urls:
            continue
        urls.add(url)
        rank = len(leads) + 1
        stable = {"query": query.query, "provider": provider, "url": url}
        content = stable | {
            "rank": rank,
            "title": candidate.title,
            "snippet": candidate.snippet,
            "retrievedAt": retrieved_at.isoformat(),
        }

        leads.append(
            SearchLead(
                title=candidate.title,
                snippet=candidate.snippet,
                url=url,
                query=query.query,
                rank=rank,
                provider=provider,
                retrievedAt=retrieved_at,
                resultId=search_digest(stable),
                resultDigest=search_digest(content),
            )
        )
        if len(leads) == maximum_results:
            break
    return SearchBatch(query=query, provider=provider, retrievedAt=retrieved_at, leads=tuple(leads))


def validate_provider_policy(budget: SearchBudget, provider: SearchProvider) -> None:
    if (
        provider.key not in budget.allowedProviders
        or provider.mode not in {"local", "remote"}
        or (provider.mode == "remote" and not budget.allowRemote)
    ):
        raise DomainError(
            "RESEARCH_SEARCH_PROVIDER_DENIED", "Search provider is outside operator policy.", 403
        )
