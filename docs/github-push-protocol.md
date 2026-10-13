# Private Git push protocol primitives

This increment encodes bounded Git protocol data. It does not open a connection,
resolve credentials, run a candidate process, register a model tool or grant
publication authority. Production push remains unavailable until the protected
native commit, validation and durable publication service are composed.

The intended transport is authenticated smart HTTPS with an operator-configured
GitHub repository. The service must independently resolve numeric repository
identity, task/runtime/lease ownership, complete publisher identities, approved
branch scope, the final native commit checkpoint and Campaign B's valid current
evidence. Neither provider packets nor a model-supplied plan may grant authority.
Credentials remain inside the trusted broker/HTTP boundary and are never given
to a Git subprocess or candidate/test workspace.

`git_protocol` implements a narrow SHA-1, protocol-v0 subset from the official
[Git HTTP protocol](https://git-scm.com/docs/http-protocol),
[push protocol](https://git-scm.com/docs/gitprotocol-pack) and
[pack format](https://git-scm.com/docs/gitformat-pack). It constructs an
undeltified pack directly from private verified object payloads, derives object
IDs from the actual bytes, and includes the pack checksum trailer. Payloads remain
excluded from request representations. Unsupported formats/capabilities refuse.

A request contains exactly one generated `refs/heads/codex/...` ref and one
direct-child commit. The remote old value comes from fresh advertisement; an
existing feature ref must equal the native parent. The wire command carries that
expected old value, which receive-pack verifies before updating the ref. A
concurrent change is rejected by the server. No deletion, force request, merge
commit, push option, arbitrary ref or command is generated. The configured base
branch is excluded by repository policy.

The parser bounds response bytes and packet counts, rejects malformed/truncated
or ambiguous streams, and requires exact single-ref status. Hook/provider error
text is never reflected in domain diagnostics. A lost or ambiguous response
must be reconciled against protected intent and authenticated remote state by
the future durable service before retry. An already-published head is not sent
again by this encoder.

Initial verification used isolated local native Git receive-pack repositories:
the exact commit/tree/blob bytes arrived, main stayed at its original commit,
and a concurrent feature ref change was preserved instead of overwritten.
Scope, malformed streams, unsupported capabilities, duplicate objects and
diagnostic redaction also passed (15 tests). This is wire compatibility proof,
not real authenticated GitHub publication acceptance.
