# Trusted source-change contracts

The `app.models.source_change` contracts and `app.source_change.planner` provide
pure UTF-8 source proposal validation. They do not read a repository, modify source,
execute commands, issue approvals, or claim that mutation is supported. There is
no HTTP importer for model-supplied safety observations. Binary source, symlinks,
submodules, renames, directory creation and arbitrary POSIX modes are unavailable.
Renames must be separately reviewed as an explicit delete and add.

## Trust boundary

A trusted caller supplies a `SourceObservation` from a future fenced native reader.
It must check every ancestor of the workspace root, include all immediate siblings
at every affected directory level, and observe new leaves as explicitly absent.
Existing changed files require exact UTF-8 contents, SHA-256, byte count and Git
regular-file mode. Unchanged sibling file metadata may omit content. Directory and
leaf reparse attributes must come from native no-follow inspection; a model's
assertion is never evidence. The pure planner cannot prove inventory completeness,
ownership, or absence of races. A future mutation adapter must retain native
directory handles/fences and repeat exact before/after verification during edits.

Plans bind repository identity, base/head commit, the digest of the entire supplied
observation, the immutable original plan hash and authorization provenance reference.
The authorization reference does **not** approve the source plan. An execution
adapter will require a separate operator approval of the resulting exact plan hash.
Nothing in these models grants authority to change a workspace.

## Deterministic representation

Inputs require canonical NFC relative paths with forward slashes. Traversal,
absolute paths, ADS, device names (including Windows superscript variants), short
name aliases, control/format characters, trailing dots/spaces, credential paths and
runtime outputs are refused. Paths are never silently rewritten. Case-folded leaf
and ancestor aliases are rejected on every platform. Source changes cannot contain
ancestor/descendant operations in one proposal. Existing parent directories are
required; links, junctions, reparse points, special files and hard-linked changed
files are refused. Add requires absence; modify/delete require exact before identity.
Mode-only changes between `100644` and `100755` are supported as proposals.

Change order is sorted by path. Fingerprints and plan hashes use sorted-key compact
JSON and SHA-256. UTF-8 bytes, CRLF/LF and final-newline differences remain exact.
Limits are 64 changed files, 65,536 bytes per file and 1,048,576 aggregate bytes,
counting both before and after. Observations have at most 4,096 metadata entries
and 1,048,576 content bytes. Empty source files are valid; NUL/binary and invalid
Unicode source are refused. Output order and fingerprints are independent of input
ordering and source observation enumeration.

The deterministic preview is an ordered metadata diff: operation, path SHA-256,
before/after content digest, byte count, mode and change fingerprint. It omits raw
paths and source so diagnostic rendering cannot expose embedded secrets. Authorized
reviewers inspect the immutable source payload in a separately protected evidence
channel. The payload contains exact source and must never be copied to audit events,
logs or public errors. `repr` hides source content. Failure messages contain fixed
codes and generic text, never input values. Pydantic error collections include raw
inputs by default: callers must never publish `errors()` or raw validation payloads.
Use the planner's sanitized `DomainError` boundary for public failures.

## Refusals and recovery

Codes distinguish invalid contracts, identity conflicts, unsafe root/parent/leaf,
incomplete observations, aliases, existing add targets, missing modify/delete targets,
before identity conflicts, denied output paths and altered plans. Verification
reconstructs the entire proposal from current observations and current policy rather
than trusting a caller's recomputed hash or `model_copy`. A refusal changes nothing.
There is no recovery position or side-effect journal in this pure milestone.

Mutation integration remains gated on merged #93 and #99. It must reuse production
workspace ownership, exact-plan operator approval, runtime/lease/attempt fencing,
durable intent, recovery checkpoints, audit and transactional outbox; shell or code
execution remains unavailable. No temporary checkout interface is introduced here.
