# Confined validation runner contracts

The runner remains disabled. There is no execution switch, caller-supplied launch
callback, executable path, shell fallback or environment flag that enables it.
`DisabledValidationRunner.execute` always returns the same isolation-unavailable
domain failure before any process or filesystem operation. The availability
contract cannot claim `enabled=true` or `isolation_implemented=true`.

This milestone provides deterministic descriptions of reviewed validation families
for backend Ruff format/check, Python pytest, frontend typecheck, ESLint, Vitest,
build, migration round trips, repository integrity and browser acceptance.
Descriptions are rebuilt from the verified execution plan, retain the complete
gate budget, and reject rehashed candidate changes. Focused pytest targets come
only from the deterministic planner and are converted to fixed relative file
arguments; publication always uses the full suite. No model can select flags,
programs, working directories, plugin expressions or scripts. These descriptions
are not launch-ready capabilities; native integrity, artifact scope, tool sealing
and isolation adapters still require implementation before an executable runner
can consume them.

The existing `self_build.owned_process` primitive and Windows Job Objects own
process lifecycle and clean up descendants. They do not isolate repository code
from user files, credentials, the network or other processes. A worktree, command
allowlist, wall timeout and memory limit are likewise insufficient. Running
pytest, a build, package scripts, test plugins or migration code runs arbitrary
repository code. They must all remain inside an actual verified isolation boundary.

Before any executable runner is introduced, demonstrate all of these properties:

- A separate security principal or VM boundary that cannot access host secrets,
  credentials, user files, runtime database, lease tokens or collector keys.
- An immutable source snapshot and sealed tool/dependency identities, with only
  the exact approved input tree readable and bounded ephemeral outputs writable.
- Network isolation that denies external access and host loopback/service access.
- Containment of child processes, escape attempts, resource exhaustion and output
  floods, with complete cleanup on normal exit, cancellation, stop or crash.
- A collector outside the untrusted boundary that measures timestamps, exit,
  resources and output digests and authenticates receipts independently.
- Existing exact-plan approval, live lease/attempt, emergency stop and durable
  intent/checkpoint checks immediately before each effect and result publication.
- Adversarial escape tests for filesystem traversal, symlink/junction escape,
  environment/credential reads, network connections, process spawning and attempts
  to forge or overwrite collector evidence.

A declared `sandbox_verified` value or model text cannot establish any of these
properties. Until a concrete platform implementation passes adversarial checks,
all families are described as unavailable. No smoke test launches candidate source
through this runtime service. Operator-approved developer gates used to validate
these PRs remain separate from Jarvis autonomous execution authority.

This PR is explicitly stacked on trusted validation evidence. It introduces no
migration or runtime-route changes. Hosted independent review is unavailable;
local review must record this limitation and confirm no reachable process launch.

## Platform investigation (2026-10-12)

Windows Sandbox is a candidate isolation layer, not an implemented Jarvis boundary.
Microsoft documents that default configurations enable networking and clipboard
sharing, and that writable mapped host folders retain guest changes. Any prototype
must explicitly disable unnecessary redirection and networking and expose only
sealed inputs. [Microsoft configuration documentation](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)

Microsoft documents AppContainer isolation for files, networking and processes.
That mechanism still needs concrete token/capability configuration, sealed paths,
child containment and adversarial verification for this workload before Jarvis can
claim it works. [Microsoft AppContainer documentation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)

The newer composable process sandbox API is explicitly experimental. Its documented
filesystem and network restrictions depend on AppContainer being enabled. It is
therefore not an appropriate automatic replacement for the current process helper
without platform compatibility and security verification.
[Microsoft experimental sandbox API documentation](https://learn.microsoft.com/en-us/windows/win32/secauthz/createprocessinsandbox)

Engineering inference: a guest-writable result file or JSON assertion alone cannot
be trusted measurement evidence, even when it came from an isolated VM. Collector
keys and the result-observation channel must remain outside candidate authority.
The current repository has neither a verified sandbox adapter nor such a configured
collector channel. This PR intentionally leaves execution unavailable.


## Local validation and review

Runner/evidence regressions: 33 passed. Exact CI collection: 2,360 tests.
Backend Ruff and frontend typecheck, ESLint, Vitest (214 tests) and build passed.
The full backend baseline from dependency #108 passed 2,350 tests with 2 skips;
this delta introduces descriptions and unconditional denial only, so focused
regressions cover it without repeating the unchanged full baseline locally.
Author-performed local review verified fixed family reconstruction, bounded
candidate iteration, canonical digest comparisons, no caller-selected executable
or launch callback, no subprocess import and no enable path. This is not hosted
independent review. Exact final-head CI is required after publication.
