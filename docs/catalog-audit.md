# Agent and Skill Catalog Quality and Security Audit

Jarvis supports importing external agent and skill definitions into a durable,
untrusted catalog. The catalog quality and security audit provides an explicit,
read-only inspection mechanism to evaluate the quality, provenance, revision status,
and runtime authorization posture of catalog records before workforce expansion.

## Principles and Security Guarantees

The catalog audit operates under strict security boundaries:

- **Strictly Read-Only**: The audit performs queries against control-plane tables
  (`catalog_sources`, `catalog_entries`, `catalog_revisions`, `catalog_activations`,
  `identity_agents`, capability/permission assignments) and never mutates state.
- **Zero Authority Granting**: The audit report and CLI script never grant permissions,
  assign roles, promote ranks, activate identities, approve revisions, execute skill
  bodies, or alter database rows.
- **Bounded and Safe Output**: Instruction text bodies, raw definitions, original source
  markdown, license texts, and potential prompt injection payloads are strictly excluded
  from audit JSON and summary outputs. Reports contain metadata, counts, identifiers,
  and categorized finding summaries only.
- **Clear Separation of Severity**: Audit findings explicitly separate confirmed unsafe
  conditions (e.g., active catalog identities with direct permission grants or system
  agent status) from informational warnings (e.g., unreviewed entries, duplicate variants,
  or unmapped capability tags).

## Command Line Interface

Operators can execute the audit against any existing migrated Jarvis control-plane
database using `scripts/audit-agent-catalog.py`.

### Prerequisites

Ensure the database URL points to an existing, migrated Jarvis SQLite database.

### Human-Readable Summary Output

By default, the CLI prints a clean, formatted text summary:

```powershell
$python = '.\apps\api\.venv\Scripts\python.exe'
$dbUrl = 'sqlite:///C:/path/to/jarvis.db'

& $python scripts/audit-agent-catalog.py --database-url $dbUrl
```

Example human-readable output:

```
=== JARVIS AGENT & SKILL CATALOG AUDIT REPORT ===
Timestamp: 2026-10-10T00:00:00+00:00

--- SUMMARY COUNTS ---
Total Entries:   202
  Agents:        110
  Skills:        92
  Discoveries:   0
Total Sources:   1
Total Revisions: 202

--- STATE BREAKDOWN ---
Review States:
  unreviewed: 202
Trust States:
  external_untrusted: 202
Activation & Identity States:
  Activated Entries:                0
  Active & Enabled Identities:      0
  Inactive / Suspended Identities:  0
  Updates Available:               0
  Enabled Entries:                 0
  Disabled Entries:                202

--- CATEGORIZED FINDINGS ---
Confirmed Unsafe Conditions (0):
  None
Informational Warnings (202):
  [WARN] 202 catalog entry revision(s) are unreviewed.

--- RECOMMENDATIONS ---
• Security: No confirmed unsafe identity authorization or privilege escalation states detected.
• Review: Perform operator review on 202 unreviewed catalog entry revision(s).
```

### Formatted JSON Output

To produce machine-readable output for programmatic inspection, pass `--json`:

```powershell
& $python scripts/audit-agent-catalog.py --database-url $dbUrl --json
```

## Report Structure

The audit output contains the following top-level sections:

1. **`summary_counts`**: Total catalog entries broken down by kind (`agent`, `skill`,
   `discovery`), as well as total sources and revisions.
2. **`state_breakdown`**:
   - Review states (`unreviewed`, `approved`, `rejected`).
   - Trust states (`external_untrusted`).
   - Activation & identity breakdown (`activated_entries`, `active_and_enabled_identities`,
     `inactive_or_suspended_identities`, `updates_available`, `enabled_entries`,
     `disabled_entries`).
3. **`provenance_findings`**: Source commit SHA validation, license check (MIT vs other),
   imported count vs revision count consistency, and orphan revision detection.
4. **`duplicate_findings`**: Explicit canonical relationships (`duplicate_of`), variant
   groupings, and fingerprint collisions.
5. **`capability_findings`**: Unmapped frontmatter tags, invalid taxonomy capabilities,
   and agent entries missing capability mappings.
6. **`security_warning_findings`**: Parser-generated warnings (e.g. ignored authority
   fields such as requested permissions or system agent overrides).
7. **`revision_findings`**: Superseded revisions and entries where active identities are
   running on an older revision (`update_available`).
8. **`active_identity_findings`**: Detailed audit of all active identities linked to catalog
   entries, asserting specialist agent type, no system agent flag, no rank, and zero direct
   permission/role grants.
9. **`categorized_findings`**: High-level grouping into `confirmed_unsafe` and
   `informational_warnings`.
10. **`recommendations`**: Advisory operator recommendations outlining review, promotion,
    or security remediation steps.

## Limits and Non-Goals

- The audit report is advisory. It does not perform auto-remediation or auto-approval.
- The audit reads stored durable records in the control-plane database only. It does not
  fetch upstream git repositories, clone external projects, or download network resources.
- Structural or authorization policy modifications (such as changing RBAC boundaries or
  permission inheritance) are beyond the scope of this audit tool.
