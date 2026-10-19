# Native Hyper-V management milestone — execution still disabled

Draft PR #134 publishes the portable contracts and quarantined collector while
preserving `21efe49` and `d3fa66d`. Its `[skip ci]` tip avoids unverified hosted
artifact/cache charges; mandatory hosted checks remain pending. No automatic
merge or review service consuming paid credits is requested.

## Concrete native interfaces implemented

`validation_runner/hyper_v.py` runs only the fixed deployed PowerShell script's
inspect, stop and unregister operations. It uses the existing immutable image
pins (including ancestors) for executable and script and the native owned-process
primitive for the management process. No guest code or arbitrary shell command
is accepted. Environment and module discovery are restricted to Windows system
locations. Output is bounded to 64 KiB, the management call times out after 30
seconds, and the caller's live authority guard runs before launch, during polling
and before return. A lost/error/timeout result is uncertain, not success.
Windows Job Objects here own the trusted management process only; the guest VM
must provide the security boundary. No adapter is registered in the application.
The full installed Microsoft module/dependency graph still requires independent
enrollment/integrity verification before production use.

`scripts/hyper-v-validation-control.ps1` queries actual Hyper-V objects by UUID,
checks the enrolled host MachineGuid and the exact ownership marker in VM notes,
then measures VM state, assigned/startup memory, processor count, host VM uptime,
host CPU usage, connected adapters and checkpoints. These counters come from the
host, not guest JSON. They cannot identify pytest exit status, per-command output
or failure classification. `HyperVObservation.command_outcome` is always
unmeasured. Do not translate VM shutdown or low CPU use into successful tests.

The adapter can force-stop an owned VM and independently re-inspect its off state.
It can unregister an already stopped owned VM. It **never deletes disks or host
directories**, and returns storage_cleanup_complete=false. VM absence must be
verified by a successful management query, not a silenced service error. Full
safe storage cleanup and restart reconciliation are still incomplete. Durable
effect intent and native acknowledgements must be supplied by existing journal
integration; no second effect ledger is created here. Cleanup authorization must
be designed to remain available to the protected supervisor after emergency stop
or executor lease expiry, without granting model authority over other VMs.

## Explicit nonbillable operator provisioning procedure

`scripts/provision-validation-vm.ps1` does nothing without
`-ApproveProvisioning`. It requires the SHA-256 of the exact reviewed plan bytes,
checks the current host MachineGuid and a pre-existing marked storage root,
requires existing image licensing confirmation with additionalSpendingCents=0,
checks bounded memory/CPU/disk and current capacity, and hashes the approved parent
VHDX while keeping its file open against writes/deletion. It creates a differencing
disk and a **stopped** Generation 2 VM with fixed memory/CPU, automatic start and
checkpoints disabled, network adapters removed and guest integration services
disabled. It enables host VM resource metering. It never installs Windows
features, reboots, downloads an image, starts a guest, or changes global Hyper-V
settings. It is not called by the application or model tools.

The operator must supply an already licensed, offline-prepared image with pinned
dependencies, no personal/host credentials, no signing key and a restricted test
account. Do not purchase Windows licenses or credits, use a trial requiring later
payment, or assume the development laptop license covers the permanent host.
Cloud inference remains independent of local test execution. The selected host,
image and storage are explicit; laptop RAM is not a default production allocation.

Before running the procedure, an administrator must independently verify the
selected host's already installed Hyper-V support and prepare a private,
collector-service-owned storage root outside personal/development workspaces,
including `.jarvis-runner-root.json` containing its hostMachineGuid. The procedure
does not create/relax those ACLs. It performs path/reparse preflight but does not
yet supply a fully proven handle-based namespace guard or atomic concurrency
reservation for provisioning. It is therefore a manual stopped-VM preparation
procedure, **not a production-safe automatic provisioning adapter**.

The plan has exactly these fields: schemaVersion="1.0", hostMachineGuid,
storageRoot, imagePath, imageSha256, vmName (`jarvis-validation-` plus 32 lowercase
hex characters), ownershipDigest, memoryBytes, cpuCount, diskBytes,
existingImageLicenseConfirmed=true and additionalSpendingCents=0. Numeric fields
must be integers. Review these values and compute the file hash locally, then the
operator explicitly invokes the procedure with that hash. Do not submit secret
image credentials or a guest command. Interrupted preparation leaves a stopped
VM/disk requiring recovery under its exact name; never repeat creation or
recursively remove an ambiguous path. No preparation action was performed here.

## Tests and exact remaining acceptance

Mocks exercise the actual PowerShell control script's inspect/stop/unregister and
foreign-owner rejection paths, without invoking Hyper-V. Python tests cover
native observation/authority fencing and refusal to produce execution results.
The optional `test_real_hyperv_operator_enrolled_vm_configuration_only` is a real
read-only VM configuration test, skipped without operator enrollment. Set
JARVIS_HYPERV_READONLY_ACCEPTANCE only to a protected JSON file with `binding`
(host_identity, host_machine_guid, vm_id and ownership/provisioning/capsule digests)
and `transport` (absolute deployed executable/script/system_root and their
digests). It permits only inspect; it does not start or provision a VM, and is
not a test of candidate containment or independently measured command exits.

Local validation: 146 runner/evidence/repair tests passed, one real-VM test was
skipped without enrollment; Ruff formatting/lint and PowerShell syntax checks
passed; frontend typecheck/ESLint/234 Vitest/build passed. No full backend suite
was repeated for this isolated internal milestone. Mock native cmdlets are not
evidence that a real Hyper-V host/guest or provisioning procedure passed.

These acceptance tests still require an actual provisioned guest and additional
native mechanisms, and **have not passed**:

| Acceptance | Required mechanism and proof |
| --- | --- |
| Host credentials inaccessible | Credential-free low-privilege guest; protected separate collector identity; attempted host/profile/environment/credential-store reads must fail |
| Filesystem escape impossible | Pinned complete capsule exporter, no-follow guest importer, no writable host mounts; malicious reparse/hardlink/ADS/archive paths cannot affect host files |
| Network isolation | No virtual NIC, redirections or alternate interfaces; external, management and host-loopback probes fail while only guest loopback serves browser checks |
| Process/resource containment | Hypervisor bounds plus sealed guest supervisor; fork/process/disk/output floods cannot escape or outlive host-controlled destruction |
| Source integrity | Complete bytes/modes independently match the approved capsule at exact committed HEAD before launch and after interruption; partial/stale/extra files deny |
| Collector forgery | Independently protected per-command telemetry/exit mechanism; fake guest JSON/output, channel substitution or supervisor tampering cannot yield an authenticated pass |
| Cancellation/timeout/restart | Durable VM identity before effect, source/approval/lease/checkpoint/stop fences, bounded stop and actual storage destruction; lost outcomes reconcile without duplicate launch |
| Broken/fixed code | A known failure and correction run inside the verified VM on distinct exact snapshots; independently measured failures and passes enter journal-backed receipts; old passing evidence is stale |

Hyper-V host counters alone cannot satisfy the forgery or broken/fixed tests.
A protected command telemetry mechanism, source transport, validated launch path
and safe complete cleanup are the next independent implementation work. Until
they and the actual guest pass acceptance, DisabledValidationRunner and the
quarantined receipt behavior remain unchanged. Neither manual developer tests
nor ordinary GitHub CI qualify as this acceptance.

Native API references: [Hyper-V module](https://learn.microsoft.com/en-us/powershell/module/hyper-v/),
[Set-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/set-vm),
[New-VHD](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vhd).
