# Native optical delivery and protected guest measurement

Media completion re-fetches the VM and revalidates exact identity/ownership,
stopped generation-2 state, absence of networking/checkpoints/enabled integration
services, fixed memory and disabled automatic startup after final drive readback.
Late isolation changes refuse the observation instead of becoming a durable
reconciliation position. Read-only inspection never attaches/detaches media or
starts the VM; deterministic native PowerShell fixtures exercise these races.

This increment is stacked on Campaign B #142 and reconciles authoritative main
at `5e11d60`. Existing Campaign B history, including `21efe49` and `d3fa66d`,
is preserved. Autonomous executable validation remains disabled.

## Implemented interfaces

- A deterministic, bounded ISO9660 image carries one literal `CAPSULE.BIN`.
  Canonical structure, exact source payload, length, digest and padding are
  checked before use. The Linux importer requires an enrolled read-only optical
  block device and imports through the existing verified source contract.
- Native Hyper-V attach/detach operations require a stopped generation-2 VM,
  original host/VM ownership, private registered media and an explicit authority
  guard. They refuse network adapters, enabled integration services, checkpoints,
  automatic startup and foreign optical media. They never start a VM.
- Native delivery intents and single-use claims use the existing transactional
  audit/outbox, lease and checkpoint machinery. Lost acknowledgments leave claims
  uncertain and unreplayable; configuration observations are not execution proof.
  Production construction requires an independently authenticated original native
  identity registry. The test fixture registry is not a production substitute.
- A recovery-only optical inspection checks the original private media and actual
  stopped-VM drive without calling attach, detach or startup. Original claims can
  transition to reconciled after lost acknowledgments; repeated recovery only
  acknowledges the same durable observation. Changed state, forged success or
  revoked authority is refused. This requires the original live lease; separately
  authorized maintenance after lease expiry is still a remaining dependency.
- A Linux root-side guest executor defines fixed catalog commands in restricted
  systemd units. It uses a separate root filesystem, read-only source, dynamic
  unprivileged users, private networking/devices, restricted namespaces and
  capabilities, bounded writable temporary directories and configurable resource
  limits. It captures output outside the command root and reads native systemd
  status and kernel cgroup measurements. Execution and cleanup authorities are
  separate; an uncertain stop requires host-side VM containment.

The guest module requires operator-pinned systemd executables, a compatible
systemd version supporting `--expand-environment=no`, a prebuilt dependency root,
and original command claims. It is not configured or deployed by this change.
Its returned observation is explicitly unmeasured and unauthorized until the
protected collector and authenticated receipt path are implemented and accepted.

## Local verification

Deterministic contract tests cover media corruption, traversal and malformed
structures, source/ownership fencing, forged native observations, concurrent
claims, lost checkpoint acknowledgments and restricted guest command profiles.
PowerShell fixture tests exercise the real management script against fake Hyper-V
cmdlets. These tests do not establish a real isolation boundary. Linux systemd,
cgroup and optical-device behavior cannot be accepted by these Windows tests.

## Remaining production work and operator actions

1. Select the actual compatible Windows host and explicitly approve provisioning
   using an already licensed, nonbillable guest image. Inspect its real resources
   and feature availability; VM CPU, memory, storage and concurrency remain
   configurable. Development-laptop acceptance does not accept a different host.
2. Implement protected native creation identity persistence, immutable media
   materialization and durable capacity reservation/recovery. Delivery currently
   assumes an already registered private media artifact. Full lifecycle recovery
   and cleanup after lease expiry still require separate maintenance authority.
3. Enroll the protected guest service and dependency root. Implement the
   authenticated control channel, independent host telemetry collector, boot
   identity and signing/verification path. No guest-reported JSON can grant a pass.
4. Compose VM startup, cancellation, timeout and restart recovery with existing
   exact-source approval, lease, checkpoint and emergency-stop checks. Keep media
   pinned throughout guest use and revalidate it after recovery. Implement the
   separate native repository-integrity gate and genuine Campaign C receipts.
5. On that provisioned host, run real acceptance for credential isolation,
   filesystem escape, unauthorized network access, process containment, source
   integrity, collector forgery, cancellation and crash recovery. Demonstrate
   failing then corrected code with independently authenticated receipts.

No features, virtual machines, images, credentials or paid infrastructure are
installed or activated by this increment. Publication uses `[skip ci]`; skipped
hosted checks are not passing validation evidence. Never enable autonomous
execution until actual-host acceptance and protected collection succeed.

Campaign repair: the native registration output watchdog starts after executable pinning and suspended-process initialization. Slow setup does not consume the running-child inactivity budget; output caps, live authority checks and owned-process cleanup remain enforced. Deterministic delayed-pin/delayed-launch regressions accompany the existing stall, overflow and emergency-stop cases.
