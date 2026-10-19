# Bounded native owned-storage cleanup

`validation_runner.cleanup.cleanup_registered_storage` implements Windows handle
deletion of an exact protected creation inventory. It is not registered in the
application and does not activate execution, provisioning or capacity reuse.
`NativeHyperVAdapter.require_absent` extends the existing fixed transport with a
fresh successful Hyper-V inventory lookup; registered VMs and service errors deny.

## Ownership and effects

The supervisor supplies a previously enrolled local dedicated root and a
`StorageRegistration` authenticated against its protected durable creation record.
It contains native volume/file IDs for the root, allocation and every artifact,
original JournalOwner/journal digest and the exact HyperVBinding. Candidate JSON,
matching filenames, self-computed digests or VM absence cannot establish ownership.
The authority callback must verify this original record, current host identity,
exclusive cleanup ownership and a persisted cleanup intent before every effect.
It must permit the narrowly scoped cleanup of owned resources after execution
lease expiry or emergency stop without granting execution or source mutation.

Only fixed local drives are supported. UNC/mapped network storage, drive roots,
relative paths, traversal, ADS names and unregistered parents are refused. The
allocation name must match the existing operator procedure's
`jarvis-validation-<32 lowercase hex>` format. At most 128 registered entries and
eight path levels are accepted. The actual dedicated root is never deleted.

All root ancestors stay pinned against replacement. Artifact handles request
read/control/delete access, reject write/delete sharing, open reparse points
without following them and verify their actual path and native identity. Regular
files must have one link. Opened-handle stream enumeration rejects alternate
streams or incomplete/unsupported inspection. Every deletable object and the
dedicated root must have the registered service SID as owner, a protected DACL,
and no allow ACE for identities other than that service, SYSTEM or Administrators.
Null/unprotected ACLs and unsupported callback/object ACEs deny; no permissions
are modified by cleanup. Privileged Windows administrators remain trusted.

The complete actual directory contents must match the registered remaining
inventory before the first deletion. Unknown files/checkpoints or replaced native
IDs cause refusal. Deletion uses the verified open handle with
`SetFileInformationByHandle(FileDispositionInfo)`, never recursive pathname removal.
Windows requires DELETE access for that operation; the dedicated-root handle
explicitly lacks it. See the [Microsoft API contract](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)
and [handle-based security inspection](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo).

Native VM absence and cleanup authority are rechecked before each deletion and
before completion. A configurable 1–300 second deadline bounds admission between
effects, with native management calls separately bounded by the existing transport.
An in-flight native call cannot be interrupted by that deadline; expiry after the
call denies the next effect and leaves reconciliation required.

## Restart and quarantine

Only native FILE_NOT_FOUND/PATH_NOT_FOUND can reconcile an already absent
registered artifact. Access denial, sharing violations and other native failures
never masquerade as absence. An interrupted deletion can resume with the same
authenticated original registration and fresh cleanup authorization. It neither
recreates missing artifacts nor launches the VM. Unknown remaining entries still
block cleanup. The allocation is removed only after registered children disappear.

The result reports `registered_storage_absent`, always `capacity_released=false`
and `execution_authorized=false`. The caller must append the observed completion
and acknowledge the existing durable checkpoint before independently confirming
all VM/other resource cleanup and releasing a real reservation. This module never
claims that an isolated execution or complete host recovery happened.

## Remaining production integration

The operator procedure currently does not persist native creation IDs, enroll
service-private ACLs, or authenticate this registration in the durable runner
history. It may leave unregistered partial artifacts after interruption. Those
artifacts must remain quarantined; do not infer ownership from directory names or
generate a registration from whatever exists at recovery time. A protected
creation/recovery supervisor must record exact original identities and effect
intent before automatic lifecycle integration is possible.

No service account, ACL enrollment, VM creation/start or real disk cleanup was
performed here. Native tests delete newly created temporary fixture files only,
with explicit mocked ACL/VM guards; a separate native ACL refusal test verifies
that an unenrolled owner is rejected without deleting anything. Deterministic ACL
tests cover protected descriptors, owner substitution, public access, unprotected
ACLs, callback ACEs and security API errors. Fixed-script tests use mocked Hyper-V
cmdlets. A positive real service-account/ACL/VM cleanup acceptance test is still
required. Initial fixture ACL enrollment attempts were denied by the local token;
no production ACL check was bypassed to make those tests pass.

Focused runner/source/cleanup/repair/receipt regressions currently pass **194 tests**
with **1 real-VM check skipped**. VM command measurement, atomic host reservations,
protected creation records and durable lifecycle composition remain unfinished.
The runner remains disabled and this is not production or live-acceptance complete.
Ruff format/lint passed for all 382 backend/script files. Frontend typecheck,
ESLint, 234 Vitest tests and build passed; the fixed PowerShell script passed AST
parsing. Git whitespace/tracked-artifact and six-file credential-pattern checks
passed. No migration/HTTP/event contract changed, and no full-suite repeat or
remote workflow run was used as evidence.
