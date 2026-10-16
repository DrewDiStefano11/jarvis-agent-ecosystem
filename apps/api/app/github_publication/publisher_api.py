"""Guarded draft PR reconciliation. No retry loop, merge or arbitrary API path."""

import json
import re
from hashlib import sha256
from urllib.parse import quote, unquote

import httpx
from pydantic import SecretStr

from app.github_publication.git_https import GitHubPushHTTPS, fail
from app.github_publication.git_protocol import MAX_RESPONSE_BYTES, OID
from app.github_publication.transport import GitHubRetry


class GitHubPublisherAPI(GitHubPushHTTPS):
    async def base_head(self):
        async def read():
            value = await self._api("GET", "/branches/" + quote(self.target.base_branch, safe=""))
            try:
                if value["name"] != self.target.base_branch or not OID.fullmatch(
                    value["commit"]["sha"]
                ):
                    raise ValueError
                return value["commit"]["sha"]
            except (KeyError, TypeError, ValueError):
                fail("GITHUB_BASE_IDENTITY_INVALID", 502)

        return await self._guarded("base_observation", None, read)

    async def _api(self, method, suffix, *, params=None, body=None):
        scope = self._scope.get()
        if scope is None:
            fail("GITHUB_PUBLICATION_FENCE_REQUIRED", 403)
        number_path = isinstance(suffix, str) and re.fullmatch(r"/pulls/[1-9][0-9]{0,18}", suffix)
        if method == "POST":
            if (
                suffix != "/pulls"
                or not isinstance(body, dict)
                or set(body) != {"title", "body", "head", "base", "draft"}
                or body["draft"] is not True
                or body["base"] != self.target.base_branch
            ):
                fail("GITHUB_PUBLICATION_EFFECT_SCOPE_INVALID", 403)
            self.target.branch(body["head"])
        elif method == "PATCH":
            if not number_path or not isinstance(body, dict) or set(body) != {"title", "body"}:
                fail("GITHUB_PUBLICATION_EFFECT_SCOPE_INVALID", 403)
        elif method == "GET":
            permitted = (
                suffix == "/pulls"
                or number_path
                or suffix == "/branches/" + quote(self.target.base_branch, safe="")
            )
            if isinstance(suffix, str) and suffix.startswith("/git/ref/heads/"):
                branch = unquote(suffix[len("/git/ref/heads/") :])
                self.target.branch(branch)
                permitted = suffix == "/git/ref/heads/" + quote(branch, safe="")
            if not permitted or body is not None:
                fail("GITHUB_PUBLICATION_EFFECT_SCOPE_INVALID", 403)
        else:
            fail("GITHUB_PUBLICATION_EFFECT_SCOPE_INVALID", 403)
        self._check(*scope)
        try:
            token = self._broker.resolve()
            if not isinstance(token, SecretStr):
                raise ValueError
            raw = token.get_secret_value()
            if not 1 <= len(raw) <= 4096 or any(not 33 <= ord(c) <= 126 for c in raw):
                raise ValueError
        except Exception:
            fail("GITHUB_CREDENTIAL_UNAVAILABLE")
        await self._identity(token)
        try:
            principal = json.loads(await self._exchange(identity=True, publisher=True, token=token))
            if type(principal.get("id")) is not int or principal["id"] not in self._publisher_ids:
                raise ValueError
        except (ValueError, AttributeError, TypeError, UnicodeError):
            fail("GITHUB_PUBLISHER_IDENTITY_CHANGED", 403)
        self._check(*scope)
        try:
            async with httpx.AsyncClient(
                base_url="https://api.github.com",
                transport=self._transport,
                timeout=20,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    method,
                    f"/repos/{self.target.owner}/{self.target.name}" + suffix,
                    params=params,
                    json=body,
                    headers={
                        "Authorization": "Bearer " + raw,
                        "Cookie": "",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                ) as response:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        self._check(*scope)
                        if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                            fail("GITHUB_RESPONSE_LIMIT", 502)
                        payload.extend(chunk)
                    if response.status_code in {429, 502, 503, 504}:
                        value = response.headers.get("retry-after", "60")
                        raise GitHubRetry(
                            min(max(int(value), 1), 86400)
                            if value.isdigit() and len(value) <= 6
                            else 60
                        )
                    if response.status_code == 401:
                        fail("GITHUB_AUTHENTICATION_REQUIRED")
                    if response.status_code == 403:
                        try:
                            error = json.loads(payload)
                        except (ValueError, UnicodeError):
                            error = None
                        secondary = (
                            isinstance(error, dict)
                            and isinstance(error.get("message"), str)
                            and "secondary rate limit" in error["message"].casefold()
                        )
                        if (
                            response.headers.get("x-ratelimit-remaining") == "0"
                            or "retry-after" in response.headers
                            or secondary
                        ):
                            raise GitHubRetry(60)
                        fail("GITHUB_ACCESS_DENIED", 403)
                    if response.status_code == 404:
                        fail("GITHUB_RESOURCE_UNAVAILABLE", 404)
                    if response.status_code not in ({201} if method == "POST" else {200}):
                        fail("GITHUB_RESPONSE_REJECTED", 502)
                    try:
                        result = json.loads(payload)
                    except (ValueError, UnicodeError):
                        fail("GITHUB_RESPONSE_INVALID", 502)
        except httpx.HTTPError:
            fail("GITHUB_TRANSPORT_UNAVAILABLE")
        await self._identity(token)
        self._check(*scope)
        return result

    async def branch_head(self, branch):
        branch = self.target.branch(branch)

        async def read():
            try:
                value = await self._api("GET", "/git/ref/heads/" + quote(branch, safe=""))
            except Exception as error:
                from app.core.errors import DomainError

                if isinstance(error, DomainError) and error.code == "GITHUB_RESOURCE_UNAVAILABLE":
                    return None
                raise
            try:
                if (
                    value["ref"] != "refs/heads/" + branch
                    or value["object"]["type"] != "commit"
                    or not OID.fullmatch(value["object"]["sha"])
                ):
                    raise ValueError
                return value["object"]["sha"]
            except (KeyError, TypeError, ValueError):
                fail("GITHUB_REMOTE_REFERENCE_INVALID", 502)

        return await self._guarded("branch_observation", None, read)

    def _verified_pr(self, value, branch, head, marker):
        try:
            number = value["number"]
            if (
                type(number) is not int
                or number <= 0
                or value["state"] != "open"
                or value.get("merged_at") is not None
                or value["draft"] is not True
                or value["head"]["ref"] != branch
                or value["head"]["sha"] != head
                or type(value["head"]["repo"]["id"]) is not int
                or value["head"]["repo"]["id"] != self.target.repository_id
                or value["base"]["ref"] != self.target.base_branch
                or type(value["base"]["repo"]["id"]) is not int
                or value["base"]["repo"]["id"] != self.target.repository_id
                or type(value["user"]["id"]) is not int
                or value["user"]["id"] not in self._publisher_ids
                or not isinstance(value["body"], str)
                or value["body"].count(marker) != 1
                or value["html_url"]
                != f"https://github.com/{self.target.owner}/{self.target.name}/pull/{number}"
            ):
                raise ValueError
            return number
        except (KeyError, TypeError, ValueError):
            fail("GITHUB_PR_RECONCILIATION_CONFLICT", 409)

    async def ensure_draft(self, branch, head, operation_digest, title, body):
        branch = self.target.branch(branch)
        if (
            not isinstance(head, str)
            or not OID.fullmatch(head)
            or not isinstance(operation_digest, str)
            or len(operation_digest) != 64
            or any(c not in "0123456789abcdef" for c in operation_digest)
        ):
            fail("GITHUB_PR_SCOPE_INVALID", 403)
        marker = "<!-- jarvis-publication:" + operation_digest + " -->"
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or any(ord(c) < 32 for c in title)
            or not isinstance(body, str)
            or not 1 <= len(body.encode()) <= 60000
            or "<!-- jarvis-publication:" in body
        ):
            fail("GITHUB_PR_METADATA_INVALID", 403)
        approved_body = body + "\n\n" + marker
        metadata = dict(
            title=title, body=approved_body, head=branch, base=self.target.base_branch, draft=True
        )
        digest = sha256(
            json.dumps(
                dict(metadata=metadata, head_sha=head), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

        async def reconcile():
            matches = []
            for page in range(1, 22):
                values = await self._api(
                    "GET",
                    "/pulls",
                    params={
                        "state": "all",
                        "head": self.target.owner + ":" + branch,
                        "base": self.target.base_branch,
                        "per_page": 100,
                        "page": page,
                    },
                )
                if not isinstance(values, list) or len(values) > 100:
                    fail("GITHUB_RESPONSE_INVALID", 502)
                # Verify the provider's filtering independently. Never adopt or
                # modify an unrelated PR returned by a broken or hostile response.
                for value in values:
                    self._verified_pr(value, branch, head, marker)
                    matches.append(value)
                if len(matches) > 1:
                    fail("GITHUB_PR_RECONCILIATION_CONFLICT", 409)
                if len(values) < 100:
                    break
            else:
                fail("GITHUB_EVIDENCE_LIMIT", 502)
            if matches:
                value = matches[0]
                number = self._verified_pr(value, branch, head, marker)
                if value.get("title") != title or value["body"] != approved_body:
                    value = await self._api(
                        "PATCH", f"/pulls/{number}", body={"title": title, "body": approved_body}
                    )
            else:
                value = await self._api("POST", "/pulls", body=metadata)
            self._verified_pr(value, branch, head, marker)
            # The acknowledgement is a fresh independent exact-head read, not
            # merely a potentially stale write response.
            verified = await self._api("GET", f"/pulls/{value['number']}")
            self._verified_pr(verified, branch, head, marker)
            if verified.get("title") != title or verified["body"] != approved_body:
                fail("GITHUB_PR_RECONCILIATION_CONFLICT", 409)
            return verified

        return await self._guarded("pr_reconcile", digest, reconcile)
