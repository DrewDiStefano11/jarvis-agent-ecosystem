from __future__ import annotations

import csv
import html
import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.runtime_supervisor.config import SupervisorConfig, SupervisorCoordination


@dataclass(frozen=True)
class AutostartStatus:
    supported: bool
    installed: bool
    task_name: str
    detail: str


def _quoted_argument(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def task_arguments(config: SupervisorConfig) -> str:
    return " ".join(
        [
            "-m",
            "app.runtime_supervisor",
            "--repository",
            _quoted_argument(str(config.repository)),
            "start",
        ]
    )


def _current_user_sid() -> str:
    try:
        completed = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("could not query the current Windows user SID") from exc
    row = next(csv.reader(io.StringIO(completed.stdout)))
    if len(row) < 2 or not row[1].startswith("S-"):
        raise RuntimeError("could not determine the current Windows user SID")
    return row[1]


def task_xml(config: SupervisorConfig, user_sid: str) -> str:
    executable = html.escape(str(config.python_executable), quote=True)
    arguments = html.escape(task_arguments(config), quote=True)
    working_directory = html.escape(str(config.api_directory), quote=True)
    sid = html.escape(user_sid, quote=True)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Starts the local-only Jarvis runtime supervisor at user logon.</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{sid}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{sid}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author"><Exec><Command>{executable}</Command><Arguments>{arguments}</Arguments><WorkingDirectory>{working_directory}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _schtasks() -> str | None:
    return shutil.which("schtasks.exe") or shutil.which("schtasks")


def status(config: SupervisorConfig | SupervisorCoordination) -> AutostartStatus:
    executable = _schtasks()
    if os.name != "nt" or executable is None:
        return AutostartStatus(False, False, config.task_name, "Windows Task Scheduler unavailable")
    try:
        completed = subprocess.run(
            [executable, "/Query", "/TN", config.task_name, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return AutostartStatus(False, False, config.task_name, "Task Scheduler query failed")
    if completed.returncode == 0:
        return AutostartStatus(True, True, config.task_name, "installed for current-user logon")
    return AutostartStatus(True, False, config.task_name, "not installed")


def install(config: SupervisorConfig) -> AutostartStatus:
    executable = _schtasks()
    if os.name != "nt" or executable is None:
        raise RuntimeError("Windows Task Scheduler is unavailable")
    if not config.python_executable.is_file():
        raise RuntimeError(f"Python executable does not exist: {config.python_executable}")
    xml = task_xml(config, _current_user_sid())
    descriptor, xml_name = tempfile.mkstemp(suffix=".xml", prefix="jarvis-supervisor-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-16", newline="\r\n") as stream:
            stream.write(xml)
        try:
            completed = subprocess.run(
                [executable, "/Create", "/TN", config.task_name, "/XML", xml_name, "/F"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Task Scheduler registration did not complete") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"Task Scheduler registration failed: {detail}")
    finally:
        Path(xml_name).unlink(missing_ok=True)
    return status(config)


def uninstall(config: SupervisorConfig | SupervisorCoordination) -> AutostartStatus:
    executable = _schtasks()
    current = status(config)
    if not current.supported or not current.installed:
        return current
    try:
        completed = subprocess.run(
            [executable, "/Delete", "/TN", config.task_name, "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Task Scheduler removal did not complete") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Task Scheduler removal failed: {detail}")
    return status(config)
