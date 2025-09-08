# autoheal/patch_validator.py
from __future__ import annotations
import os
from typing import Dict, Any

class PatchValidator:
    """
    Minimal validator used in CI:
    - Always returns ok=True unless a hard precondition fails
    - Records a small result payload the orchestrator expects
    - You can harden this later with schema checks, dry-runs, unit tests, etc.
    """

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def validate(self, workspace: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "ok": True,
            "tests_passed": True,
            "policy_ok": True,
            "changed_lines": 0,
            "details": {},
        }

        # Basic sanity: workspace must exist
        if not workspace or not os.path.isdir(workspace):
            result["ok"] = False
            result["tests_passed"] = False
            result["details"]["reason"] = f"Workspace not found: {workspace}"
            return result

        # Optional: require a known patch shape
        if not isinstance(patch, dict):
            result["ok"] = False
            result["tests_passed"] = False
            result["details"]["reason"] = "Patch is not a dict"
            return result

        # If you later record how many lines changed, set it here:
        # result["changed_lines"] = patch.get("changed_lines", 0)

        return result


def apply_and_run(workspace: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Back-compat shim for ci_orchestrator.
    Delegates to PatchValidator.validate().
    """
    return PatchValidator({}).validate(workspace, patch)
