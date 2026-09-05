# Workspace execution acceptance

Checkpoint status: the harness passes static checks. Its integrated browser run
has not yet been executed at this checkpoint; a successful run must produce the
`acceptance.json` evidence described below before claiming end-to-end validation.

Install the backend environment and frozen frontend dependencies first. Run from
the integrated repository with Business Lab and workspace tools present:

```powershell
.\apps\api\.venv\Scripts\python.exe scripts/smoke-workspace-tools.py --provider fixture
```

This mode uses a deterministic HTTP planning fixture. It then exercises the real
API, separate worker process, browser authorization controls, bounded filesystem
tools, persisted report, and API restart. It must not be reported as real model
inference.

To test an already installed local Ollama model, select it explicitly:

```powershell
.\apps\api\.venv\Scripts\python.exe scripts/smoke-workspace-tools.py --provider ollama --ollama-base-url http://127.0.0.1:11535 --model qwen3.5:0.8b
```

The script checks the specified service's installed-model list. It does not
install a model, start Ollama, or fall back to fixture output. A model that cannot
produce the requested bounded plan fails acceptance before tool authorization.
Use `--repository-root C:\path\to\integrated-repository` when the harness and
application checkout are temporarily separate.

Both modes allocate their own database, marked workspace, and loopback API/UI
ports. Database and data-directory variables are set before setup or application
imports. In the browser, the harness creates a Business Lab objective, prepares
its planner, queues workspace planning, checks the persisted proposal, inspects
its content, and explicitly authorizes exactly three steps: list `inputs`, read
`inputs/brief.txt`, and write a report to `reports/plan.md`.

The report is based on facts already supplied in the objective. File reads are
observations; this flow does not claim adaptive reasoning over newly read data.
The harness checks the report's actual bytes and SHA-256 against its API artifact,
unchanged input, source and child task completion, desktop/mobile rendering, and
exact persisted model/execution/artifact records after restarting the API.

The printed temporary evidence directory retains service logs, browser logs,
screenshots, an execution receipt, the test database, and marked workspace.
`acceptance.json` is written only after all checks pass and records whether the
run used actual Ollama or fixture inference. All processes started by the harness
are stopped afterward. The chosen existing Ollama service remains running.
