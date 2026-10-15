# Exact source payload implementation

`app.validation_runner.source_transfer` extends the existing capsule verifier.
It is not registered as an application tool and cannot launch a VM or command.
The default runner remains disabled. No VM or host configuration was changed.

## Capture and wire format

`export_snapshot` requires the existing verified plan, JournalOwner, journal
digest and independently measured complete native inventory. Its trusted
`authority_check(root, capsule)` must check the registered workspace/root,
repository/head and current source inventory, exact operator approval, attempt,
lease, policy and emergency stop. A caller-selected path or self-computed digest
does not substitute for those checks. Production composition with the native
state reader and runtime fence remains necessary; no permissive default exists.

Each ancestor is opened with the existing no-follow directory primitives.
Windows holds directory handles against replacement; POSIX traverses descriptors.
The exporter and guest importer preflight all distinct directory prefixes plus
root ancestry against a 128-directory pin budget before opening any pins. A
schema-valid capsule exceeding that resource policy refuses before reads/writes
instead of failing partway through materialization with descriptor exhaustion.
The Linux importer also checks every UTF-8 path component against the actual
destination's `PC_NAME_MAX` before any directory/file creation. Unsupported name
limits and overlong components refuse without partially materializing earlier
entries; the capsule schema's character limit alone is insufficient.
Regular source files must have one link, no reparse point and the approved hash
and length. POSIX additionally verifies executable bits; Windows Git modes are
taken from the independently measured native inventory, not filesystem flags.
The exporter reads only approved inventory entries. Git metadata, credential
paths, databases and generated/private output remain denied by CapsuleFile.
An operator must also exclude secrets embedded in otherwise ordinary source;
filename filtering cannot prove that arbitrary source contains no credentials.

`freeze_payload` verifies the entire bounded inventory before returning immutable
bytes. Duplicate/unexpected entries are rejected before accumulation. The payload
contains a versioned magic prefix, a bounded canonical capsule manifest and file
bytes in inventory order. There are no extra wire filenames, extraction commands,
compression, guest results or self-authenticating authority claims.

`receive_payload` requires the independently supplied expected capsule. It compares
the manifest exactly without parsing untrusted JSON, verifies every content hash,
and refuses trailing, missing, corrupt or differently bound bytes. Manifest
bindings include the existing plan/source hash, complete inventory and JournalOwner
(workspace/task/run/attempt/worker/approval/lease). Repository and Git head are
bound by the verified plan's CodeState; independently verifying the native Git
tree and complete inventory remains the source reader's responsibility.

## Linux guest materialization

`import_linux_guest` is executable implementation for an enrolled Linux guest
service, not a Windows importer. It refuses other platforms and non-root service
identities. It verifies the full payload before filesystem writes, then requires
an empty 0700 root owned by root under root-owned ancestors without group/world
write access. All traversal uses held directory descriptors. Directories and
files are created exclusively; existing entries, links or cross-device content
are refused. Files are reopened and remeasured, exact modes and directory entries
are checked, and files/directories are sealed 0444/0555 with fsync before return.
Cancellation leaves partial artifacts quarantined; no recursive removal or
automatic retry occurs. A returned capsule digest describes source import only.

This service must run before any candidate process exists. Later candidate code
must use a separate unprivileged identity with no write authority over the source,
measurement service or its protected control channel. Root privileges alone do
not attest a guest agent: image/service integrity, privilege restrictions, an
authenticated control channel and real adversarial acceptance are still required.
The importer is not deployed or invoked here. Its syscall tests are deterministic
fixtures; they do not establish Linux guest filesystem containment.

## Outstanding native integration

There is no Hyper-V payload delivery channel yet. A production transport must
carry the sealed bytes one way (for example independently verified read-only
media), carry the expected capsule through a protected authenticated channel,
verify the enrolled guest service and bind its acknowledgment to the existing
durable effect/checkpoint history. It must never mount the working repository
writable in the guest. Windows guest importing needs equivalent exclusive,
handle-relative creation and ADS/reparse enumeration; it presently fails closed.

There are still no real command execution receipts, automatic VM startup,
capacity reservations or complete disk cleanup. These functions grant none of
those capabilities. Campaign C still requires fresh exact-commit validation;
precommit-source import does not authorize publication of a later commit.

Local tests cover payload integrity, workspace/approval/attempt/lease substitution,
bounded duplicate rejection, immutable capture after later host modification,
native Windows hard-link refusal, revocation, and simulated descriptor import,
unexpected entries, occupied paths, unsafe permissions and interrupted writes.
Real guest importer, containment and broken-code/repaired-code acceptance remain
blocked on explicitly approved environment enrollment and the missing execution,
protected measurement and cleanup integration.

## Local validation of this increment

Focused runner, repair and receipt regressions: **172 passed, 1 real-VM check
skipped**. One existing Pydantic serializer-warning test remains expected. Ruff
format/lint passed for all 380 backend/script files. Frontend typecheck, ESLint,
234 Vitest tests and build passed using existing dependencies. Git whitespace,
tracked-artifact and bounded credential-pattern checks passed. No migration or
HTTP/event contract was changed, and no repeated full backend suite was run.
Native Windows fixture reads needed execution outside the filesystem sandbox to
resolve genuine file handles. Guest importer syscall fixtures are simulations.
None of these results is isolated execution evidence or live VM acceptance.
