# Campaign A coding provider boundary

The preferred future coding provider is the existing native Codex app-server
integration coordinated with Campaign D. Production inference is quarantined:
`build_development_router` always returns `None`, and the production executor
always refuses before admission or reservation, including when an enabled
configuration or injected router is supplied. No setting, model response, or
subscription identity can establish billing authority.

Campaign A does not read OpenAI API key settings, construct an API client, or use
the newly provisioned key. Separately billed API inference is prohibited. Live
inference remains disabled until independent account-specific evidence proves
included-only subscription usage, exclusion of purchased credits, and enforceable
recharge safeguards. Campaign D owns that provider/activation boundary; private
provider code is not imported as an available merged capability.

`codex_app_server` is the preferred configuration identity, not an assertion that
live dispatch is available. Model identity must be explicit. Remote routing and
fallback remain disabled. Future integration must preserve one physical request,
exact task/context/request identity, complete usage, bounded output/deadline,
cancellation, sanitized failure and uncertain-outcome refusal. The model receives
no authority to execute commands or mutate files; proposals still pass native
source verification and separate exact-plan operator approval.

The earlier proposal-only Responses adapter remains an unwired protocol module.
Its fixed endpoint, closed proposal schema, response-size limits, identity/usage
checks, secret screening and cancellation are exercised only through deterministic
HTTP `MockTransport` fixtures with dummy keys. This module's presence is not
permission to use the API. Tests never load the provisioned key. Test-only executor
subclasses permit only explicitly injected mock transports; the production class
has no fixture or activation switch.

The unwired adapter additionally enforces the central execution policy before
client creation or source upload, even with enabled settings or an injected
client. Execution integration fixtures explicitly forbid constructing real HTTP
transports while exercising mocked wire behavior; unmocked adapter/factory
regressions independently assert production refusal. Admission digests include
the execution/configuration and mutation-contract implementation dependencies.

Fixture responses establish protocol and recovery behavior, not real AI coding
quality. Native filesystem mutation and inspection in those tests are real, but
mock provider output is not independently verified code-execution evidence.
Campaign B must still run its real isolated validation. Implementation completion
and live coding acceptance are separate, and neither is claimed by this milestone.
