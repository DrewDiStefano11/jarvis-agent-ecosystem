from dataclasses import dataclass
from typing import List, Dict, Optional
from urllib.parse import urlparse
import re

from .enums import ToolRiskLevel, NetworkPolicy

@dataclass
class ToolDefinition:
    name: str
    risk_level: ToolRiskLevel
    requires_approval: bool
    supported_in_phase_2b: bool
    path_bearing_parameters: List[str]
    url_bearing_parameters: List[str]

TOOL_REGISTRY: Dict[str, ToolDefinition] = {
    # GREEN
    "read_approved_file": ToolDefinition("read_approved_file", ToolRiskLevel.GREEN, False, True, ["path"], []),
    "list_approved_directory": ToolDefinition("list_approved_directory", ToolRiskLevel.GREEN, False, True, ["path"], []),
    "search_approved_text": ToolDefinition("search_approved_text", ToolRiskLevel.GREEN, False, True, ["path"], []),
    "read_task_history": ToolDefinition("read_task_history", ToolRiskLevel.GREEN, False, True, [], []),
    "read_existing_artifact": ToolDefinition("read_existing_artifact", ToolRiskLevel.GREEN, False, True, ["path"], []),

    # YELLOW
    "create_sandbox_artifact": ToolDefinition("create_sandbox_artifact", ToolRiskLevel.YELLOW, False, True, ["path"], []),
    "update_sandbox_artifact": ToolDefinition("update_sandbox_artifact", ToolRiskLevel.YELLOW, False, True, ["path"], []),
    "create_patch_proposal": ToolDefinition("create_patch_proposal", ToolRiskLevel.YELLOW, False, True, [], []),

    # ORANGE
    "apply_patch_to_workspace": ToolDefinition("apply_patch_to_workspace", ToolRiskLevel.ORANGE, True, True, ["path"], []),
    "create_git_branch": ToolDefinition("create_git_branch", ToolRiskLevel.ORANGE, True, True, [], []),
    "create_email_draft": ToolDefinition("create_email_draft", ToolRiskLevel.ORANGE, True, True, [], []),
    "create_calendar_draft": ToolDefinition("create_calendar_draft", ToolRiskLevel.ORANGE, True, True, [], []),

    # RED (Not supported in Phase 2B)
    "shell": ToolDefinition("shell", ToolRiskLevel.RED, True, False, [], []),
    "powershell": ToolDefinition("powershell", ToolRiskLevel.RED, True, False, [], []),
    "install_package": ToolDefinition("install_package", ToolRiskLevel.RED, True, False, [], []),
    "git_push": ToolDefinition("git_push", ToolRiskLevel.RED, True, False, [], []),
    "git_merge": ToolDefinition("git_merge", ToolRiskLevel.RED, True, False, [], []),
    "delete_workspace_file": ToolDefinition("delete_workspace_file", ToolRiskLevel.RED, True, False, ["path"], []),
    "send_email": ToolDefinition("send_email", ToolRiskLevel.RED, True, False, [], []),
    "modify_calendar": ToolDefinition("modify_calendar", ToolRiskLevel.RED, True, False, [], []),
    "browser_automation": ToolDefinition("browser_automation", ToolRiskLevel.RED, True, False, [], []),
    "desktop_control": ToolDefinition("desktop_control", ToolRiskLevel.RED, True, False, [], []),
    "credential_read": ToolDefinition("credential_read", ToolRiskLevel.RED, True, False, ["path"], []),

    # BLACK (permanently prohibited)
    "disable_audit": ToolDefinition("disable_audit", ToolRiskLevel.BLACK, True, False, [], []),
    "change_approval_policy": ToolDefinition("change_approval_policy", ToolRiskLevel.BLACK, True, False, [], []),
    "self_grant_permissions": ToolDefinition("self_grant_permissions", ToolRiskLevel.BLACK, True, False, [], []),
    "read_credentials": ToolDefinition("read_credentials", ToolRiskLevel.BLACK, True, False, ["path"], []),
    "transfer_money": ToolDefinition("transfer_money", ToolRiskLevel.BLACK, True, False, [], []),
    "automatic_purchase": ToolDefinition("automatic_purchase", ToolRiskLevel.BLACK, True, False, [], []),
    "security_setting_change": ToolDefinition("security_setting_change", ToolRiskLevel.BLACK, True, False, [], []),
}

def is_path_safe(path: str, workspace_roots: List[str]) -> bool:
    """
    Validates a path against safety rules and workspace roots.
    """
    if "\0" in path:
        return False
    # No absolute Windows paths
    if re.match(r"^[a-zA-Z]:[\\/]", path):
        return False
    # No absolute POSIX paths
    if path.startswith("/"):
        return False
    # No UNC paths
    if path.startswith(r"\\"):
        return False
    # No traversal
    normalized_separators = path.replace("\\", "/")
    parts = normalized_separators.split("/")
    if ".." in parts or "." in parts:
        return False
    if "%" in path: # simple encoded rejection
        return False
    if "$" in path or "~" in path: # env var / home dir
        return False

    # Must fall within allowed roots (simplified for prototype)
    if not workspace_roots:
        return True # If none specified, assume all relative paths valid for prototype? Wait, requirements say: "Paths outside task-envelope workspace roots". If workspace roots exist, must check.

    # Check if the path starts with any of the allowed workspace roots
    # E.g. path="approved/input.txt", workspace_roots=["approved/"]
    for root in workspace_roots:
        root_norm = root.replace("\\", "/")
        if not root_norm.endswith("/"):
            root_norm += "/"
        if normalized_separators.startswith(root_norm) or normalized_separators == root.rstrip("/"):
            return True

    # Also allow if workspace root is just the directory itself
    # E.g., workspace_roots=["approved"] and path="approved/input.txt"
    for root in workspace_roots:
         if normalized_separators.startswith(root + "/"):
             return True

    return False


def is_url_safe(url: str, network_policy: NetworkPolicy) -> bool:
    """
    Validates a URL against the network policy.
    """
    if len(url) > 2000:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""

    if scheme in ["file", "data", "ftp"]:
        return False

    if "@" in parsed.netloc: # credentials in URL
        return False

    if network_policy == NetworkPolicy.NONE:
        return False

    if network_policy == NetworkPolicy.LOOPBACK:
        if host not in ["localhost", "127.0.0.1", "::1"]:
            return False

    # Metadata IPs
    if host in ["169.254.169.254"]:
        return False

    if scheme not in ["http", "https"]:
        return False

    return True
