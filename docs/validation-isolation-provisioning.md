# Portable local validation isolation — disabled pending acceptance

This milestone preserves commit `21efe49`. No guest, Windows feature, cloud
resource, model credit or paid CI/storage service is activated. No executable
runner is registered. `DisabledValidationRunner` still denies every execution.

## Host discovery and architecture

The machine inspected on October 13 is the **development laptop**, not the
unconfirmed permanent Jarvis host. Read-only discovery found Windows 11 Pro
10.0.26200, an active hypervisor and 68,103,761,920 bytes physical memory.
WindowsSandbox.exe, the Hyper-V PowerShell module, vmms and vmcompute were absent.
Optional-feature discovery requires an administrator token even outside the tool
sandbox. Feature state therefore remains **unknown**, not proven disabled.
CPU virtualization/SLAT flags reported false under the active hypervisor; this
does not prove physical hardware lacks virtualization. No production-host
hardware, capacity or isolation acceptance is inferred from these observations.

Run `scripts/probe-validation-isolation.ps1 -DeploymentRole development` locally,
or `-DeploymentRole production` on the intended host, from an administrator
PowerShell to obtain feature state. The script is read-only and defaults to
unclassified. It neither elevates itself nor downloads, configures or launches
anything. Available CPU/storage reservations still need a native inventory of
the actual chosen host and storage volume; this diagnostic JSON is not authority.

Windows Sandbox is the first local no-purchase candidate to assess because it
uses the existing Windows installation. Its documented configuration permits
network, vGPU, clipboard/device redirection disablement, protected client mode
and memory selection. It does not by itself establish all required CPU, process,
disk, interruption/recovery and independent process-result measurements.
See [Microsoft Sandbox configuration](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file).
Do not silently enable network, writable mappings or clipboard for result capture.

A disposable Hyper-V VM is the preferred architectural candidate if Sandbox
cannot prove every requirement. It allows a separately prepared immutable guest
image, disconnected virtual NICs, fixed memory/CPU and bounded disposable disks.
It is not available on this laptop as inspected. A Windows guest requires an
existing valid license whose use adds **zero** spending; the host's Pro edition
does not establish that entitlement. A free Linux guest could cover portable
gates but cannot substitute for mandatory Windows-native acceptance.
See [Hyper-V requirements](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/host-hardware-requirements).
Neither backend is selected as a proven production implementation yet.

## Implemented interfaces

`models/validation_isolation.py` defines bounded provisioning limits and explicit
zero-spending/no-network/no-credentials/no-redirection controls; a complete source
capsule bound to original journal owner/approval/lease, journal digest and exact
execution plan/source hash; and lifecycle records with no execution authority.
Memory, CPU, disk, process/time/output ceilings and concurrency are configurable.
Concurrency defaults to one. Policy ceilings are not laptop hardware assumptions.

`validation_runner/isolation.py` validates complete inventory fingerprints,
changed/deleted files, modes and transfer byte hashes without host extraction.
Native source capture must supply the independently measured complete inventory
digest while ownership/source/Git metadata pins remain held. This does not supply
a native capsule exporter or guest no-follow filesystem importer. A truncated
manifest cannot be authorized by rehashing it. No tar/zip/path traversal importer
or arbitrary host path is accepted.

The lifecycle reducer enforces ordering, exact owner/digest, bounded sequence and
live trusted guards before/after transitions. Uncertain effects can move toward
cleanup, never relaunch. It has no VM side effects and is not a second scheduler
or persistent journal. Future native adapters must bind these records to the
existing validation journal, transactional audit/outbox and acknowledged native
checkpoints. A `destroyed` declaration alone is not proof that a VM was deleted.

Read-only host admission checks fresh physical/free RAM, logical/available CPUs,
available storage, enabled required feature, services and virtualization. It
accounts for configured concurrency and configurable host memory/storage reserves.
Unknown/stale/insufficient capacity blocks. A successful assessment grants no
launch authority; the real host adapter must atomically reserve/recheck resources.
Host identity and development/production role remain explicit. Development
acceptance never transfers to a different production host.

`validation_runner/collector.py` implements bounded, guarded output hashing and
the existing receipt HMAC protocol outside candidate inputs. Guest JSON/stdout is
never parsed as a result. Raw output is discarded, keys are not included in
records, foreign capsule/plan/owner state is denied, and guards are rechecked
during capture and before/after signing. **All receipts are quarantined as
unmeasured**, with no exit/resource success claims, so they cannot qualify a gate.
There is no isolated process telemetry adapter or passing-receipt producer yet.

`validation_runner/credentials.py` reads only the fixed generic Credential Manager
target `Jarvis.ValidationCollector.v1`, as base64 encoding of a 32-byte key, under
the intended separate collector service logon identity. No environment variable,
workspace file, arbitrary target, default key or enrollment endpoint exists.
Native credential buffers are freed. This is not configured or acceptance-tested
with a real production key. Credential Manager does not isolate keys from
same-user host code; candidate code must never run on that host identity.
See [CredReadW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw).

## Local verification and real acceptance

Deterministic mocks test credential/path escape rejection, link and mode changes,
missing/duplicate/extra source transfer, network/redirection denial, resource and
process-limit refusal, uncertain lifecycle cleanup, lease/stop guard interruption,
output floods, forged passing results and native credential API buffer release.
Portable admission tests use synthetic lower-memory and development/production
hosts. These prove interface behavior, **not real containment or process escape
prevention**. The existing runner/repair/evidence modules passed 138 local tests,
Ruff formatting/lint passed, and frontend typecheck/ESLint/234 Vitest/build passed.
The preceding integrated baseline completed all four backend shards with 2,765
passes and 3 skips. Those full-shard results predate this new contract milestone;
they are not a full-suite result for the new code. No candidate code is executed
by these new interfaces.

## Shortest path to real acceptance

1. Identify the actual target host; run the read-only administrator probe. Confirm
   supported hardware, available RAM/storage, required feature state and zero-cost
   guest/image licensing. If required, the operator enables only the selected
   Windows feature and handles any restart; no unverified host is auto-modified.
2. Prepare an existing licensed dependency image separately from candidate runs,
   with pinned tool/image identities, no personal credentials or collector key.
   Choose one lightweight VM when real capacity suffices. Cloud AI inference is a
   separate service and receives no local VM execution authority.
3. Implement/verify the native hypervisor lifecycle and guest low-privilege test
   account, fixed limits, no NIC/redirected devices/host filesystem, immutable
   source transfer and independently protected telemetry channel. Candidate tests
   must not modify the collector/guest supervisor or authenticate their own exit.
4. Deploy the separate protected collector identity/key; bind launch identity and
   exact capsule/plan to acknowledged journal intent. Reuse original approvals,
   lease/attempt, checkpoint and emergency-stop fences. Prove uncertain restart
   reconciliation and actual destruction, not merely a descriptive transition.
5. Attack the real guest: host credential reads, reparse/hardlink/path escapes,
   external/host-loopback networking, process escape, disk/output floods, forged
   results, collector compromise, stop and host restart. Then run deliberately
   broken and corrected code with independently measured exact-head receipts.
   Repeat host-specific acceptance on the permanent host before enabling it.

Campaign C must capture and freshly validate committed head C; parent P's receipts
are stale after commit. No protected derivation currently promotes P receipts.
Mock/quarantined receipts, historical verdicts, manual developer tests and ordinary
GitHub CI cannot establish the real isolated execution prerequisite for publication.
Publishing this milestone also requires explicit push approval and confirmation
that hosted CI/storage adds no charge; no new hosted run is triggered here.
