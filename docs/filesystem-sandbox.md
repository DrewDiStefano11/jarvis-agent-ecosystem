# Filesystem sandbox

The filesystem sandbox is the required boundary for future agent-facing file operations. It
provides a provider-neutral `FilesystemSandbox` protocol and a secure local provider without
exposing host paths or raw operating-system calls to agents. Phase 2 does not expose this
provider as an agent tool or HTTP API; `create_app` constructs it and places it in
`app.state.filesystem_sandbox` for future service-layer adapters.

## Architecture

`app.filesystem.protocols` defines the stable provider contract and metadata shape.
`LocalFilesystemSandbox` implements that contract under one configured root.
`SandboxConfiguration` converts validated application settings into provider configuration.
Sandbox errors extend the existing `DomainError` envelope and use stable
`FILESYSTEM_*` codes.

Future services should depend on `FilesystemSandbox`, not `LocalFilesystemSandbox`, `Path`,
`open`, `os`, or `shutil`. A cloud or remote provider can implement the same protocol while
retaining its own path and concurrency controls.

```python
from app.filesystem import FilesystemSandbox


def store_report(filesystem: FilesystemSandbox, content: bytes) -> None:
    filesystem.create_directory("reports", parents=True)
    filesystem.write_file(
        "reports/summary.txt",
        content,
        overwrite=False,
    )
```

All paths passed to the interface are portable, sandbox-relative paths. File payloads are
bytes so encoding decisions remain at the service boundary.

## Operations

The provider supports:

- reading, writing, and appending regular files;
- creating and deleting directories, with recursive deletion only when explicitly requested;
- copying regular files;
- moving and renaming files or directories;
- listing directories, checking existence, and reading metadata;
- normalizing and centrally validating every caller-supplied path.

Writes do not replace an existing destination unless `overwrite=True` is passed. Parent
directories are not created unless `create_parents=True` or `parents=True` is passed.
Copies currently support regular files; directory trees can be added to a future provider
contract only with explicit size, symlink, and partial-failure semantics.

## Security model

The local provider:

- rejects absolute, UNC, drive-qualified, empty, dot, and parent-traversal paths;
- rejects control characters, Windows reserved names, trailing spaces/dots, and
  platform-unsafe filename characters on every platform;
- canonicalizes paths beneath the configured root and verifies the common root;
- rejects symbolic links and Windows filesystem reparse points in existing path components;
- uses no-follow file descriptors for reads and appends where the operating system supports
  them;
- reserves its staging directory and hides configured restricted directories from access and
  listings;
- enforces optional extension allowlists and maximum sizes on reads and mutations;
- requires explicit overwrite authorization and supports a read-only mode;
- stages replacement content, flushes it to disk, and atomically replaces the destination;
- uses atomic hard-link creation for non-overwriting writes;
- serializes operations across all local provider instances that use the same canonical root.

The sandbox root is a trust boundary. Administrators must give the Jarvis process exclusive
write control of it. The provider revalidates links on every operation, but Python does not
offer one portable directory-handle API that eliminates every intermediate-component race
against a separate hostile host process, especially on Windows. Remote and multi-process
providers must supply equivalent provider-specific locking or conditional-write guarantees.

Appending uses `O_APPEND` and an in-process root lock. It prevents lost appends between
Jarvis threads, but an operating-system crash can still leave a partially appended record.
Callers that need transactional records should write a complete replacement or use durable
database storage.

Metadata creation time is based on the host's `st_ctime`; its precise meaning is
platform-dependent. File contents are never included in logs.

## Configuration

Configuration is validated when `Settings` is created and the root is initialized when the
application is created.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `JARVIS_SANDBOX_ROOT` | `./data/sandbox` | Host directory containing all sandbox data |
| `JARVIS_SANDBOX_MAXIMUM_FILE_SIZE` | `10485760` | Maximum read, write, or resulting append size in bytes |
| `JARVIS_SANDBOX_ALLOWED_EXTENSIONS` | empty | Comma-separated allowlist such as `txt,json`; empty allows all |
| `JARVIS_SANDBOX_RESTRICTED_DIRECTORIES` | empty | Comma-separated sandbox-relative directory prefixes denied to callers |
| `JARVIS_SANDBOX_TEMPORARY_DIRECTORY` | `.sandbox-tmp` | Reserved sandbox-relative staging directory |
| `JARVIS_SANDBOX_READ_ONLY` | `false` | Deny every mutation when true; the root must already exist |

Restricted and temporary directories may not overlap. Restricted and temporary values must
be non-empty relative paths without dot or parent segments. Extension matching is
case-insensitive.

## Errors and logging

Expected failures are deterministic `FilesystemSandboxError` subclasses for invalid paths,
boundary violations, read-only mutations, missing/existing paths, oversized files, and host
operation failures. Host permission, disk-full, and read-only-filesystem failures are
translated without exposing absolute host paths.

Structured log records use the `jarvis.filesystem` logger and include `sandbox_event`,
relative `sandbox_path`, operation metadata, and sizes where applicable. Creation,
modification, deletion, copy/move, invalid paths, denied overwrites, read-only writes,
oversized files, and unexpected host failures are logged. No metrics backend exists in this
phase; the structured event names are the integration seam for future counters and audit
sinks.

## Extension rules

New agent-facing filesystem behavior must:

1. enter through `FilesystemSandbox`;
2. perform authorization in a service before calling the provider;
3. retain explicit overwrite and recursive-delete intent;
4. add traversal, link, concurrency, failure, and configuration regression tests;
5. avoid returning the configured host root or logging file contents.

Per-agent quotas, identity-aware access policy, encryption, content scanning, durable
multi-process locks, directory-tree copy, and remote object storage are deliberate future
extensions. They are not represented as active capabilities.
