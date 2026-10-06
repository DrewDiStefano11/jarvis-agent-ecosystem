# Backend CI scalability

Backend CI runs four **serial pytest processes on independent Windows runners**.
Ruff/collection, blank-database migrations, and each shard run as separate jobs.
The existing required `backend` check aggregates their outcomes and verifies
that the exact union of collected node IDs equals full-suite collection once.
The fixed four-shard matrix uses `fail-fast: false`; existing per-branch/PR
concurrency still cancels superseded workflow runs.

## Reproduce locally

Install the Python 3.12 backend dev dependencies, then run from the repository root:

```powershell
python -m pip install -e './apps/api[dev]'
python -m ruff format apps/api scripts --check
python -m ruff check apps/api scripts
python scripts/backend_ci.py check
python scripts/backend_ci.py migrations
python scripts/backend_ci.py runtime
python scripts/backend_ci.py autonomy
python scripts/backend_ci.py models
python scripts/backend_ci.py system
```

`--artifacts PATH` selects an evidence directory; the default is the ignored
`.local/backend-ci/COMMAND`. Every pytest/migration invocation uses a fresh
temporary directory. No database, migration state, or application state is cached
or uploaded. `setup-python` caches only pip data keyed by `apps/api/pyproject.toml`.
Ordinary `python -m pytest` remains available without the CI watchdog policy.

## Coverage and rebalancing

`scripts/backend_ci_shards.json` lists every backend test file exactly once.
All commands reject unassigned, duplicate, stale, or empty assignments. The
`check` command collects the whole API tree using pytest's actual configuration
and rejects files absent from the manifest. Each shard verifies its actual
collected files, and the aggregate compares the complete node-ID sets (including
parametrized cases) from uploaded `collected.json` artifacts.

To reproduce aggregate validation, put the full-collection evidence in
`backend-collection/` and the four shard evidence directories in
`backend-runtime/`, `backend-autonomy/`, `backend-models/`, `backend-system/`,
then run `python scripts/backend_ci.py verify --artifacts PARENT`.

The labels identify stable entry points, not exclusive domain categories.
Assignments used longest-file-first balancing of measured setup/call/teardown
time, with large files spread across runners. Keep all files enabled; add new
files explicitly to a shard, and use `timings.json` to rebalance as the suite grows.
The guard fails if a newly added file has no assignment.

## Slow tests and hang policy

Each shard prints verbose test IDs and the slowest 50 pytest phases. Evidence
includes `pytest.log`, `progress.jsonl` (flushed before setup/call/teardown and
after each phase), `timings.json` (file totals and slowest complete tests),
`collected.json`, command outcome JSON, and JUnit XML. On failure inspect the last
phase record and the stack dump in `pytest.log`. A `sessionfinish` record followed
by a command timeout distinguishes interpreter/process shutdown from an active
test. Successful runs report setup + call + teardown totals per file.

CI uses `pytest-timeout`'s portable thread method with a conservative **300-second
whole-test bound**, including fixtures. It dumps all thread stacks and exits the
pytest process when exceeded. It does not interrupt a SQLite transaction and then
continue testing against that state. Hard exit can prevent teardown and JUnit
completion; partial progress/log files survive. The parent command tracks its own
descendants and terminates tracked survivors on exit, and discards temporary
databases. Regression tests exercise setup, call, teardown, command deadlines,
child cleanup, and durable partial evidence on Windows.

A legitimately slower integration test may use `@pytest.mark.timeout(600)` with
an adjacent explanation and measured evidence. Do not disable timeouts or add
exceptions to conceal hangs. No existing test needed an exception. The command
deadline is 25 minutes (also covers collection and interpreter shutdown), with a
30-minute shard job limit allowing setup and evidence upload. Static and migration
jobs have 10-minute limits; the aggregate has a 2-minute limit.

The migration command preserves upgrade head, current, history, downgrade
`20260729_04`, and re-upgrade head. Each phase has a separate log, measured duration,
and 120-second subprocess deadline. The first nonzero exit stops the gate. The
absolute SQLite path is validated through SQLAlchemy before execution.

See [pytest-timeout's termination contract](https://github.com/pytest-dev/pytest-timeout)
for why a hard timeout can leave JUnit incomplete.

## Evidence from October 6, 2026

The initial fetched base was `9d5eab1`; the implementation refreshed to
`1ee7a3c` after replanning and research-policy merges. The initial suite collected
1,527 cases in 4.41s and completed locally on Windows/Python 3.12 in **837.22s**:
1,525 passed, 2 existing environment skips (unconfigured local model and unavailable
file symlink privileges; junction coverage still ran). A separate profile of the
four newly merged files passed 107 cases in 50.57s. The initial PR collection includes
**1,661 cases**, including 27 new CI-tool regression cases.

The previous hosted [successful backend job](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/37056517399/job/111002448819)
took 42m21s overall, with pytest running 41m27s (1,296 passed, 1 skipped in the
test summary). An available older failing job spent 45m41s in pytest and reported
ordinary assertion/path failures, not a watchdog stack trace. Recent failing-job
log endpoints returned 404, so no claim of a diagnosed historical hang is made.
The [confirmed timeout job](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/37460259639/job/112257886993)
ran for 60m13s; GitHub annotated that it exceeded the 1h limit. Its log shows
pytest from 12:01:58 to 13:01:18 UTC, with continuing dot progress toward roughly
93% and no reported assertion failure. The blank migration phase had completed
before pytest started. This is evidence of accumulated suite cost, not a proven
individual hung test. All eight active jobs inspected had completed migrations
in 5–9s and were in pytest.

Before restructuring, the unchanged migration path took 4.61s locally
(upgrade 1.41s, current 0.86s, history 0.38s, downgrade 0.94s, re-upgrade 1.03s).
No pathological migration, lock, or unbounded transform was reproduced; migration
code and downgrade safety remain unchanged. The separate gate now makes a future
slow phase immediately visible and bounded.

Measured local validation of the initial shard selection (four independent processes,
each with fresh temp paths on the same Windows machine):

| Check | Collected cases | Measured execution |
| --- | ---: | ---: |
| Ruff | — | 0.22s |
| Full collection and coverage | 1661 | 4.19s |
| Migrations, sum of five subprocesses | — | 5.13s |
| runtime | 503 | 270.00s |
| autonomy | 387 | 266.02s |
| models | 467 | 257.62s |
| system | 304 | 249.97s |

Local concurrent timings include contention on one machine; they are not hosted
runner measurements or an overall hosted gating claim. The PR records actual
hosted per-job and gating timings once exact-head CI finishes.

The largest baseline files were diagnostics (131.21s), coordination (116.54s),
independent verification (69.89s), autonomous worker (61.51s), identity/RBAC
(56.66s), diagnostics reports (55.04s), and persistence (48.77s). The slowest call
was diagnostics CLI/report at 21.49s, followed by the fake-provider live probe at
15.69s. Review of sleeps/polling found bounded synchronization, fake-provider
timeout exercises, process cleanup, and repeated isolated migration/application
setup. No test assertions, recovery windows, production transactions, migrations,
or provider behavior were weakened for speed; no xdist was introduced.

The first hosted PR run exposed a Windows cleanup failure after successful test
sessions: waiting on recorded, exited descendant PIDs could encounter a recycled
protected process. Cleanup now checks process creation identity and waits only
for descendants it terminated. Six additional regression cases cover retired
PIDs, cleanup deadlines, and preservation of real permission failures. Updated
collection includes 1,667 cases and 33 CI-tool regression cases.

The first hosted run measured pytest session times of 657.67s (runtime),
986.16s (autonomy), 620.69s (models), and 1252.03s (system). Only system's
job passed: runtime/models failed in cleanup after passing tests, and autonomy
reported three failures in the new watchdog regression harness. The harness now
uses an explicit temporary pytest config/root to handle C: temp files with a D:
checkout. Application assertions remain unchanged. The final assignments use
these hosted file totals, spreading office, identity/RBAC, persistence, and
correction costs across independent runners. Local and exact-head hosted
validation are repeated after this rebalance; PR evidence records final timings.


## Hosted timing reconciliation after Mission Control rebases

Run [37498110186](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/37498110186) on exact feature head `3f1a84980f113a3c20f6fe8726bcc4cfab932537` exposed two distinct issues in the inherited sharded checks:

- Runtime stopped at the 300-second per-test watchdog in `test_operator_control_blocks_correction_dispatch[stop]`. That async test directly awaited the simulator from the pytest loop while TestClient's lifespan dispatcher owned its event loop. The test now invokes the real emergency-stop HTTP route through TestClient and verifies HTTP 200 before retaining its worker refusal and no-extra-dispatch assertions. Production code and stop authority are unchanged. The standalone old test passed locally; the hosted cross-loop timeout is the failure evidence.
- Models reached the 1,500-second process watchdog with passing progress at 54%. Flushed phase records show all 32 admission tests took 481.96 seconds and the first 75 of 84 independent-verification tests took 411.28 seconds. Autonomy completed in about 808 test seconds and system in about 675. Admission now belongs to autonomy and independent verification to system. This balances measured file costs while keeping serial execution, exact collected-test union, all tests, and both timeout limits intact.

Fresh exact-head hosted checks are required to establish merge readiness. These estimates do not guarantee runner timing: admission puts autonomy near 1,290 seconds, so inspect the next completed timings at a natural checkpoint. Old monolithic backend runs are not authoritative for reconciled feature heads.
