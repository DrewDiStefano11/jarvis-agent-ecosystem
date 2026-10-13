"""Fixed-host, bounded authenticated reads. No caller-supplied URLs or Git commands."""

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.errors import DomainError
from app.github_publication.credentials import CredentialBroker


@dataclass(frozen=True)
class RepositoryTarget:
    repository_id: int
    owner: str
    name: str
    base_branch: str
    branch_prefix: str
    minimum_approvals: int = 1
    mandatory_checks: tuple[str, ...] = (
        "backend",
        "frontend",
        "repository-integrity",
        "runtime-browser",
    )

    def __post_init__(self):
        if (
            type(self.repository_id) is not int
            or self.repository_id <= 0
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", self.owner)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", self.name)
            or not safe_branch(self.base_branch)
            or not self.branch_prefix.endswith("/")
            or not safe_branch(self.branch_prefix + "candidate")
            or self.base_branch.startswith(self.branch_prefix)
            or not self.branch_prefix.startswith("codex/")
            or type(self.minimum_approvals) is not int
            or not 1 <= self.minimum_approvals <= 100
            or not isinstance(self.mandatory_checks, tuple)
            or len(self.mandatory_checks) > 256
            or len(set(self.mandatory_checks)) != len(self.mandatory_checks)
            or any(
                not isinstance(context, str) or not 1 <= len(context) <= 200
                for context in self.mandatory_checks
            )
        ):
            raise DomainError("GITHUB_REPOSITORY_POLICY_INVALID", "Invalid repository policy.")

    def branch(self, value):
        if (
            not safe_branch(value)
            or not value.startswith(self.branch_prefix)
            or value == self.base_branch
        ):
            raise DomainError(
                "GITHUB_BRANCH_SCOPE_DENIED", "Branch is outside publication scope.", 403
            )
        return value


def safe_branch(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) is not None
        and all(
            part and not part.startswith(".") and not part.endswith((".", ".lock"))
            for part in value.split("/")
        )
        and ".." not in value
        and "//" not in value
    )


class GitHubRetry(DomainError):
    def __init__(self, retry_after_seconds=60):
        super().__init__("GITHUB_RETRY_REQUIRED", "GitHub request must be retried later.", 503)
        self.retry_after_seconds = retry_after_seconds


class GitHubReader:
    def __init__(self, target: RepositoryTarget, broker: CredentialBroker, *, transport=None):
        self.target = target
        self._broker = broker
        # No inherited proxy, netrc, environment credentials, cookies or redirects.
        self._client = httpx.Client(
            base_url="https://api.github.com",
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._prefix = f"/repos/{target.owner}/{target.name}"

    def close(self):
        self._client.close()

    def _get(self, suffix, *, params=None):
        return self._request("GET", self._prefix + suffix, params=params)

    def _request(self, method, path, *, params=None, body=None):
        try:
            token = self._broker.resolve()
            raw_token = token.get_secret_value()
            if (
                not raw_token
                or not raw_token.isascii()
                or any(character.isspace() for character in raw_token)
            ):
                raise ValueError
        except Exception:
            raise DomainError(
                "GITHUB_CREDENTIAL_UNAVAILABLE", "Operator credential unavailable.", 503
            ) from None
        try:
            with self._client.stream(
                method,
                path,
                params=params,
                json=body,
                headers={
                    "Authorization": "Bearer " + token.get_secret_value(),
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Cookie": "",
                },
            ) as response:
                if response.status_code in {429, 502, 503, 504} or (
                    response.status_code == 403
                    and (
                        "retry-after" in response.headers
                        or response.headers.get("x-ratelimit-remaining") == "0"
                    )
                ):
                    retry = response.headers.get("retry-after", "60")
                    seconds = (
                        min(max(int(retry), 1), 86400)
                        if retry.isdigit() and len(retry) <= 6
                        else 60
                    )
                    reset = response.headers.get("x-ratelimit-reset", "")
                    if (
                        response.headers.get("x-ratelimit-remaining") == "0"
                        and reset.isdigit()
                        and len(reset) <= 12
                    ):
                        seconds = min(86400, max(seconds, max(1, int(reset) - int(time.time()))))
                    raise GitHubRetry(seconds)
                if response.status_code == 401:
                    raise DomainError(
                        "GITHUB_AUTHENTICATION_REQUIRED",
                        "GitHub credential expired or was revoked.",
                        503,
                    )
                if response.status_code == 403:
                    # GitHub can omit rate headers for a secondary limit. Read
                    # only bounded provider data and retain no diagnostic prose.
                    payload = bytearray()
                    for chunk in response.iter_bytes():
                        if len(payload) + len(chunk) > 1048576:
                            raise DomainError(
                                "GITHUB_RESPONSE_LIMIT", "GitHub response exceeded its bound.", 502
                            )
                        payload.extend(chunk)
                    try:
                        error = json.loads(payload)
                    except (ValueError, UnicodeError):
                        error = None
                    message = error.get("message") if isinstance(error, dict) else None
                    if isinstance(message, str) and "secondary rate limit" in message.casefold():
                        raise GitHubRetry(60)
                    raise DomainError(
                        "GITHUB_ACCESS_DENIED", "GitHub repository access denied.", 403
                    )
                if response.status_code == 404:
                    raise DomainError(
                        "GITHUB_RESOURCE_UNAVAILABLE", "GitHub resource unavailable.", 404
                    )
                if response.status_code != 200:
                    raise DomainError(
                        "GITHUB_RESPONSE_REJECTED", "Unexpected GitHub response.", 502
                    )
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 1048576:
                        raise DomainError(
                            "GITHUB_RESPONSE_LIMIT", "GitHub response exceeded its bound.", 502
                        )
                try:
                    return json.loads(payload)
                except (ValueError, UnicodeError):
                    raise DomainError(
                        "GITHUB_RESPONSE_INVALID", "Invalid GitHub response.", 502
                    ) from None
        except httpx.HTTPError:
            # HTTP exceptions retain credential-bearing requests. Never propagate.
            raise DomainError(
                "GITHUB_TRANSPORT_UNAVAILABLE", "GitHub connection unavailable.", 503
            ) from None

    def repository(self):
        payload = self._get("")
        if (
            not isinstance(payload, dict)
            or payload.get("id") != self.target.repository_id
            or payload.get("full_name", "").casefold()
            != f"{self.target.owner}/{self.target.name}".casefold()
        ):
            raise DomainError(
                "GITHUB_REPOSITORY_IDENTITY_CHANGED", "GitHub repository identity changed.", 409
            )
        return payload

    def pull_request(self, number):
        if type(number) is not int or number <= 0:
            raise DomainError("GITHUB_PR_INVALID", "Invalid PR number.")
        return self._get(f"/pulls/{number}")

    def protection(self):
        return self._get("/branches/" + quote(self.target.base_branch, safe="") + "/protection")

    def base_ref(self):
        return self.base_details()["sha"]

    def base_details(self):
        payload = self._get("/branches/" + quote(self.target.base_branch, safe=""))
        try:
            sha = payload["commit"]["sha"]
            if payload["name"] != self.target.base_branch or not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise ValueError
            protected = payload["protected"]
            if type(protected) is not bool:
                raise ValueError
            return dict(sha=sha, protected=protected)
        except (KeyError, TypeError, ValueError):
            raise DomainError(
                "GITHUB_BASE_IDENTITY_INVALID", "GitHub base branch identity unavailable.", 502
            ) from None

    def _pages(self, suffix, key=None, params=None):
        entries = []
        for page in range(1, 22):
            payload = self._get(suffix, params={**(params or {}), "per_page": 100, "page": page})
            values = (
                payload.get(key)
                if key and isinstance(payload, dict)
                else payload
                if key is None
                else None
            )
            if not isinstance(values, list) or len(values) > 100:
                raise DomainError("GITHUB_RESPONSE_INVALID", "Invalid GitHub collection.", 502)
            entries.extend(values)
            if len(entries) > 2048:
                raise DomainError(
                    "GITHUB_EVIDENCE_LIMIT", "GitHub evidence exceeded its bound.", 502
                )
            if len(values) < 100:
                return entries
        raise DomainError("GITHUB_EVIDENCE_LIMIT", "GitHub pagination exceeded its bound.", 502)

    def rules(self):
        return self._pages("/rules/branches/" + quote(self.target.base_branch, safe=""))

    def review_threads(self, number):
        if type(number) is not int or number <= 0:
            raise DomainError("GITHUB_PR_INVALID", "Invalid PR number.")
        query = """query($owner:String!,$name:String!,$number:Int!,$after:String){
          repository(owner:$owner,name:$name){databaseId
            pullRequest(number:$number){headRefOid baseRefName
              reviewThreads(first:100,after:$after){
                pageInfo{hasNextPage endCursor}
                nodes{id isResolved comments(first:1){nodes{
                  databaseId originalCommit{oid} pullRequestReview{databaseId}
                }}}
              }
            }
          }
        }"""
        entries, after, cursors = [], None, set()
        head = None
        for _ in range(21):
            payload = self._request(
                "POST",
                "/graphql",
                body={
                    "query": query,
                    "variables": {
                        "owner": self.target.owner,
                        "name": self.target.name,
                        "number": number,
                        "after": after,
                    },
                },
            )
            try:
                if not isinstance(payload, dict) or payload.get("errors"):
                    raise ValueError
                repository = payload["data"]["repository"]
                pr = repository["pullRequest"]
                if (
                    repository["databaseId"] != self.target.repository_id
                    or pr["baseRefName"] != self.target.base_branch
                ):
                    raise ValueError
                if head is not None and pr["headRefOid"] != head:
                    raise ValueError
                head = pr["headRefOid"]
                connection = pr["reviewThreads"]
                nodes, info = connection["nodes"], connection["pageInfo"]
                if (
                    not isinstance(nodes, list)
                    or len(nodes) > 100
                    or type(info["hasNextPage"]) is not bool
                ):
                    raise ValueError
                entries.extend(nodes)
                if len(entries) > 2048:
                    raise ValueError
                if not info["hasNextPage"]:
                    return head, entries
                after = info["endCursor"]
                if not isinstance(after, str) or not after or len(after) > 500 or after in cursors:
                    raise ValueError
                cursors.add(after)
            except (KeyError, TypeError, ValueError):
                raise DomainError(
                    "GITHUB_REVIEW_EVIDENCE_INVALID", "Incomplete GitHub review evidence.", 502
                ) from None
        raise DomainError("GITHUB_EVIDENCE_LIMIT", "GitHub pagination exceeded its bound.", 502)

    def checks(self, sha):
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise DomainError("GITHUB_HEAD_INVALID", "Invalid commit SHA.")
        return self._pages(f"/commits/{sha}/check-runs", "check_runs", {"filter": "latest"})

    def statuses(self, sha):
        if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise DomainError("GITHUB_HEAD_INVALID", "Invalid commit SHA.")
        return self._pages(f"/commits/{sha}/statuses")

    def reviews(self, number):
        if type(number) is not int or number <= 0:
            raise DomainError("GITHUB_PR_INVALID", "Invalid PR number.")
        return self._pages(f"/pulls/{number}/reviews")

    def comments(self, number):
        if type(number) is not int or number <= 0:
            raise DomainError("GITHUB_PR_INVALID", "Invalid PR number.")
        return self._pages(f"/pulls/{number}/comments")

    def eligible_reviewer(self, login, user_id):
        if (
            not isinstance(login, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?", login)
            or type(user_id) is not int
            or user_id <= 0
        ):
            raise DomainError("GITHUB_REVIEWER_INVALID", "Invalid reviewer identity.", 502)
        try:
            payload = self._get("/collaborators/" + quote(login, safe="") + "/permission")
        except DomainError as error:
            if error.code in {"GITHUB_ACCESS_DENIED", "GITHUB_RESOURCE_UNAVAILABLE"}:
                return False
            raise
        try:
            if (
                payload["user"]["id"] != user_id
                or payload["user"]["login"].casefold() != login.casefold()
            ):
                raise ValueError
            return payload["permission"] in {"write", "admin", "maintain"}
        except (KeyError, TypeError, ValueError, AttributeError):
            raise DomainError(
                "GITHUB_REVIEWER_INVALID", "Reviewer permission identity unavailable.", 502
            ) from None
