# Native validation consumer reconfiguration

Trusted publication configuration replaces the native commit validation consumer
on every successful configuration, including setting it to None when disabled
or omitted. The native execution route then fails closed with
GIT_COMMIT_VALIDATION_UNAVAILABLE before native intent or effects.

Focused API regression covers initial installation, explicit disable, rejection
of stale reuse, replacement installation, and omission-based disable. Three
native/publication API tests passed with isolated temporary databases. Existing
receipt, permission, lease, plan and recovery checks are unchanged. No fixture
consumer is production authority or evidence of a real native commit.

The frontend tree is unchanged; prior successful typecheck, ESLint, Vitest and
build evidence is retained. From the profile-revocation branch onward, configuration
retains `ConfiguredNativeConsumer` around a supplied adapter and assigns None when
disabled. The API regression checks replacement delegates through that wrapper,
disabling through the HTTP route, and revocation of captured wrappers after both
explicit and omission-based disable. The profile revocation regression suite is
retained alongside these API checks.
