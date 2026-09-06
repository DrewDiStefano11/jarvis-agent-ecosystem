# External catalog and workforce (Milestone 60)

Jarvis owns identities, capabilities, permissions, lifecycle, tasks, leases and
execution. Catalog imports add untrusted data. They never activate an identity,
grant a permission, install a dependency, execute upstream scripts, or register
external tools. Normal startup and planning read local durable records only.

## Sources and provenance

| Source | Pinned commit | License | Canonical paths |
| --- | --- | --- | --- |
| `wshobson/agents` | `a30778f8c4e6b0a87567941b7cca4f534bf642b6` | MIT, Seth Hobson | `plugins/*/agents/*.md`, `plugins/*/skills/*/SKILL.md` |
| `VoltAgent/awesome-agent-skills` | `e4f7a502a78253550890e8b356d43f50192415ae` | MIT, VoltAgent | `README.md` discovery index |

The wshobson plugin directories are canonical source definitions. Generated
editor/plugin copies, commands, hooks, scripts and configuration are excluded.
The inspected pin has no nested license overrides. Acquisition refuses additional
license/notice files until their scope is reviewed. License notices are retained
both with each durable source and under `docs/third-party/` for the two small
upstream test fixtures.

VoltAgent contains a directory of links, not locally authored skill bodies.
Its MIT license covers the index, not every linked repository. The index is
preserved as one disabled discovery record. No third-party link is fetched or
approved. The detail response exposes up to 100 extracted references and the
complete original index; these references confer no authority.

`SourceAdapter` supplies repository metadata and canonical path classifications.
Acquisition is separate from the neutral `SourceSnapshot`, frontmatter parser,
deterministic taxonomy, and persistence. New adapters do not need to replace
Jarvis identity or runtime services. Network acquisition is an explicit operation
against allowlisted codeload repositories and an exact 40-character commit.
The CLI can instead read committed Git objects from a local source clone;
uncommitted file edits do not change the imported snapshot. It never executes
the clone's scripts or hooks. Compressed and selected expanded input are bounded.

## Durable model and revision semantics

Migration `20260906_09` adds `catalog_sources`, `catalog_entries`,
`catalog_revisions`, and `catalog_activations`. Agent, skill and discovery entries
are distinct discriminated concepts with shared provenance storage. They are
separate from `identity_agents`.

Sources retain repository, commit, license text, import timestamp, imported count
and a whole-snapshot hash. Revisions retain exact original text and its SHA-256,
source path, parser/taxonomy version and normalized metadata. SQLite triggers
prevent changing source provenance or revision payloads. Review state is
independent of immutable source content. Natural unique keys and the existing
serialized SQLite command pattern make concurrent imports and activations
converge. Audit records and sequenced outbox events commit with catalog commands.

A repeated pin is a no-op. Different bytes or a changed file manifest under an
already imported pin fail with `CATALOG_SOURCE_CONFLICT`. A new pin creates a new
unreviewed revision. Existing enabled state, active identity profile and capability
assignments stay unchanged. The activation relationship retains its exact previous
revision, and the UI shows an update is available. Older pins already seen do not
roll back the current revision on replay.

Review requires the exact current revision ID and an operator reason. Approval
enables the catalog entry but does not create an identity or change its trust:
the definition remains `external_untrusted`. Rejection disables the entry and
suspends its linked active identity. Unknown licensing and discovery records
cannot be approved for activation.

Activation requires a reviewed, enabled, licensed canonical agent. It uses
`IdentityService.create_agent_in_session` and `transition_in_session` inside the
catalog transaction; the original identity endpoints use those same methods.
It creates a specialist, never a system agent, with no rank, role, permission,
team, supervisor or workspace authorization. Capabilities use existing
`identity_capabilities` and `identity_agent_capabilities` tables. Tool mentions
are only `requested_tool_classes`. A role already linked to a catalog identity
cannot acquire a second identity through another entry. Existing Jarvis manager
identities are preserved.

Explicit promotion of a newly reviewed revision replaces only catalog-owned
capability assignments and updates the exact activation revision; operator
profile edits and other assignments remain intact. Activation does not resume a
suspended/retired/disabled identity. Deactivation suspends through existing
lifecycle controls. Restoring an identity is an explicit operator lifecycle
action. An inactive catalog entry remains excluded from workforce context even
if its identity is manually resumed.

Empty catalogs support downgrade/re-upgrade. A populated catalog refuses
downgrade to avoid losing provenance and activation history. Empty catalog tables
can downgrade to revision 08 even when tool-execution data prevents a subsequent
downgrade below 08; that preexisting tool-data protection remains unchanged.

## Taxonomy, variants and bounded planning context

`app/catalog/taxonomy.py` defines 25 declared capabilities and their parent
categories: management, research, business, software, data, content and operations.
Mappings use reviewed aliases for names, plugin names and source tags. They never
use model output or treat instructions as capability authority. Unmapped tags
remain visible. Plugin prefixes are removed only when the frontmatter name is
exactly the plugin name followed by the actual agent filename stem.

Variants share a normalized kind/role fingerprint. The first imported canonical
entry is retained and subsequent variants point to it; fresh full-source imports
process sorted paths deterministically. All source revisions remain stored.
This is a conservative role-based grouping, not semantic similarity scoring.
An activation-time check also prevents duplicate identity roles after revisions
change names.

Hierarchy matching is explicit: a declared descendant such as
`software.backend.api` can satisfy `software.backend` or `software`; siblings do
not imply one another. This helper is a contract for Milestone 61, not a team
selector. Skills reference applicable classes, source references and requested
tools. Agent revisions reference same-plugin skill stable keys for future
progressive disclosure. Skill bodies are loaded only by explicit detail reads.

The PR 59 context enrichment path calls `IdentityService.workforce_snapshot`.
Two SQL queries return at most 20 active/enabled identities and 12 effective
capabilities per identity. Lifecycle and catalog availability are filtered before
the limit. System identities sort first, then stable keys. The snapshot contains
role, source, operational status and exact activated revision metadata, with an
explicit sample limit and possible-truncation flag. Twenty is an existing context
budget, so a 22-agent workforce is a bounded sample; callers needing the complete
workforce must use paginated catalog/identity APIs. No automatic selection occurs.

Names and labels are serialized as JSON in an `external_content` source, not
trusted configuration: reading text from the database does not upgrade its trust.
Original instructions, skill bodies and dormant catalog records are absent from
normal planning context. Existing context token budgets, redaction, scope checks
and system safety policy still apply. Capabilities are descriptive; existing
task/project authorization checks remain required for actual execution.

## Explicit operator workflow

Run with the repository backend virtual environment after the normal Jarvis
initialization/migration process. The database URL must identify an existing
migrated Jarvis control-plane database. The importer does not initialize or
reset a database. Substitute your intended database URL for the example.

```powershell
$python = '.\apps\api\.venv\Scripts\python.exe'
$catalogDatabase = 'sqlite:///C:/path/to/operator-selected/jarvis.db'
& $python scripts/import-agent-catalog.py --source wshobson-agents `
  --ref a30778f8c4e6b0a87567941b7cca4f534bf642b6 `
  --database-url $catalogDatabase --dry-run
```

Dry-run performs validation, normalization, variant detection and change reporting
without writing source, revision, identity, permission, audit or outbox rows.
Remove `--dry-run` to import. Use `--local-tree C:/path/to/source-clone` for offline
acquisition. Repeat with source `voltagent-skills` and its exact pin above for the
discovery index. No import activates workers.

```powershell
& $python scripts/curate-agent-workforce.py --database-url $catalogDatabase
# Review the returned roles, hashes, warnings, coverage and gaps first.
& $python scripts/curate-agent-workforce.py --database-url $catalogDatabase `
  --approve-plan '<exact-plan-hash-from-reviewed-output>'
```

The curated configuration proposes 22 distinct specialist roles across all 25
capabilities. It contains no system manager replacement or permission grants.
`docs/catalog/initial-workforce.json` records the validated pinned proposal.
Each review/activation is an atomic, repeatable command; a bulk curation run can
stop after earlier agents commit if a later identity is suspended or another
operator updates a revision. Resolve the reported conflict and replay the exact
proposal, rather than resetting identities. No unattended curation runs at startup.

The initial workforce was activated and restart-tested in an isolated acceptance
database, not in the operator's clean runtime clone. Production activation remains
the explicit workflow above. Registered specialists remain offline until existing
authorized runtime activity changes their operational state. They cannot execute
tasks merely because the catalog marks them active.

## HTTP and UI

All responses use the existing `data`/`meta.schemaVersion=1.0` envelope and domain
error handling. OpenAPI models in `app/models/catalog.py` are authoritative.
Existing loopback operator control-plane middleware protects these routes, as it
does identity-management routes. They do not add a remote administration surface
or a model-callable authority endpoint.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/catalog/entries?kind=agent&offset=0&limit=25` | Metadata page; kind can also be skill/discovery |
| GET | `/api/catalog/entries?kind=agent&active_only=true` | Active catalog workforce, using activated revision |
| GET | `/api/catalog/entries/{id}` | Current definition, provenance and last 100 revision IDs |
| GET | `/api/catalog/sources` | Paginated pinned source imports |
| GET | `/api/catalog/capabilities` | Taxonomy with explicit parents |
| POST | `/api/catalog/import`, `/api/catalog/refresh` | Explicit acquisition and import; dry-run defaults true |
| POST | `/api/catalog/entries/{id}/review` | Exact revision approval/rejection with reason |
| POST | `/api/catalog/agents/{id}/activate` | Exact reviewed revision activation/promotion |
| POST | `/api/catalog/agents/{id}/deactivate` | Disable catalog entry and suspend linked identity |

Mutation identities are the persisted source pin and revision/entry IDs; repeated
calls converge without inventing a separate command store. Import HTTP bodies
accept only a registered source, full commit and dry-run flag; arbitrary local
paths or uploaded authority fields are not accepted. Local source paths are
available only in the operator CLI.

The Agents page adds Active catalog workforce, Agent Catalog, Skills, Skill
discovery, and Sources. Domain state lives in `src/state/useCatalogState.ts`
through AppStore. Pages load 25 records on demand. Detail displays original text
as escaped text, warnings, requested tools, hash, pin, path and license; review
and activation are separate buttons. Source UI shows the latest 25 imports;
older source imports and historical revision IDs remain available through APIs.

## Validation and deliberate limits

Focused tests include realistic MIT-licensed upstream fixtures, malicious
frontmatter, missing provenance, safe unknown labels, zero-write dry-runs,
immutable revisions, concurrent import/activation, rollback, restart, real HTTP
context assembly and 220 prompt-heavy dormant agents. The 220-entry case checks
pagination and exactly two workforce queries, with no prompt bodies in context.

The pinned acceptance imported 202 agents and 183 skills with 65 retained variants,
plus one VoltAgent discovery index. Import took approximately 1.1 seconds and
0.06 seconds respectively on the local test machine. Reimport converged and 22
curated identities survived reload. This is an observed local measurement, not a
throughput guarantee.

No automatic team/skill selection, task decomposition, new runtime, cloud fallback,
MCP, arbitrary shell execution, vector storage or external integration was added.
The source registry is intentionally small. Reference files named by skill bodies
are not fetched transitively. Operator review is required for new revisions and
new licensing situations. A future milestone can add richer variant resolution,
historical revision browsing and broader licensed source adapters without changing
Jarvis's authorization boundary.
