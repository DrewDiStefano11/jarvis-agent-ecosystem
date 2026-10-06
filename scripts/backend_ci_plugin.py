"""CI-only flushed phase evidence, coverage guard, and duration summaries."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path

import pytest
from backend_ci import API, load_shards


class Evidence:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.artifacts = Path(os.environ["BACKEND_CI_ARTIFACTS"])
        self.shard = os.environ["BACKEND_CI_SHARD"]
        (self.artifacts / "progress.jsonl").write_text("", encoding="utf-8")
        self.files: dict[str, float] = defaultdict(float)
        self.tests: dict[str, float] = defaultdict(float)
        self.count = 0

    def record(self, **values) -> None:
        with (self.artifacts / "progress.jsonl").open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    dict(
                        shard=self.shard,
                        elapsed=time.monotonic() - self.started,
                        **values,
                    )
                )
                + "\n"
            )

    def pytest_collection_finish(self, session) -> None:
        groups = load_shards()
        all_files = {path for files in groups.values() for path in files}
        expected = all_files if self.shard == "check" else set(groups[self.shard])
        actual = {Path(item.path).relative_to(API).as_posix() for item in session.items}
        if actual != expected:
            raise pytest.UsageError(
                f"Collected file coverage mismatch: missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
            )
        self.count = len(session.items)
        self.record(
            phase="collected",
            count=self.count,
            collection_seconds=time.monotonic() - self.started,
        )
        (self.artifacts / "collected.json").write_text(
            json.dumps([item.nodeid for item in session.items], indent=2) + "\n",
            encoding="utf-8",
        )

    def pytest_runtest_logstart(self, nodeid, location) -> None:
        self.record(phase="start", nodeid=nodeid)

    def pytest_runtest_setup(self, item) -> None:
        self.record(phase="setup_start", nodeid=item.nodeid)

    def pytest_runtest_call(self, item) -> None:
        self.record(phase="call_start", nodeid=item.nodeid)

    def pytest_runtest_teardown(self, item) -> None:
        self.record(phase="teardown_start", nodeid=item.nodeid)

    def pytest_runtest_logreport(self, report) -> None:
        self.files[report.nodeid.split("::", 1)[0]] += report.duration
        self.tests[report.nodeid] += report.duration
        self.record(
            phase=report.when,
            nodeid=report.nodeid,
            duration=report.duration,
            outcome=report.outcome,
        )

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        summary = {
            "shard": self.shard,
            "count": self.count,
            "elapsed": time.monotonic() - self.started,
            "exitstatus": int(exitstatus),
            "files": dict(
                sorted(self.files.items(), key=lambda item: (-item[1], item[0]))
            ),
            "slowest_tests": sorted(
                self.tests.items(), key=lambda item: (-item[1], item[0])
            )[:50],
        }
        (self.artifacts / "timings.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        self.record(phase="sessionfinish", exitstatus=int(exitstatus))

    def pytest_terminal_summary(self, terminalreporter) -> None:
        terminalreporter.section(
            f"Backend CI {self.shard}: file durations (setup + call + teardown)"
        )
        for path, seconds in sorted(
            self.files.items(), key=lambda item: (-item[1], item[0])
        ):
            terminalreporter.write_line(f"{seconds:8.2f}s {path}")


def pytest_configure(config) -> None:
    config.pluginmanager.register(Evidence(), "backend-ci-evidence")
