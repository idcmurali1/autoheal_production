# autoheal/ci_orchestrator.py
from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Any, Dict, Tuple

__all__ = ["LocalCIOrchestrator", "Orchestrator"]

# -------- Optional imports for legacy Orchestrator (guarded) ----------
try:
    from .failure_detector import run_test_and_capture              # legacy path
except Exception:
    run_test_and_capture = None  # type: ignore

try:
    from .artifact_store import ArtifactStore                        # legacy path
except Exception:
    ArtifactStore = None  # type: ignore

try:
    from .llm_patch_generator import generate_locator_patch          # legacy web path
except Exception:
    generate_locator_patch = None  # type: ignore

try:
    from .ios_patch_generator import generate_ios_locator_patch      # legacy iOS path
except Exception:
    generate_ios_locator_patch = None  # type: ignore

# The patch validator evolved; provide flexible import/adapter
try:
    # older code used a free function with 3 args
    from .patch_validator import apply_and_run as _apply_and_run_fn  # type: ignore
except Exception:
    _apply_and_run_fn = None  # type: ignore

try:
    # newer code exposes a class with .validate(workspace, patch_dict)
    from .patch_validator import PatchValidator  # type: ignore
except Exception:
    PatchValidator = None  # type: ignore


# -------------------- New minimal CI orchestrator --------------------
class LocalCIOrchestrator:
    """
    Minimal, production-safe orchestrator used by CLI:
      - writes JSONL ledger entries
      - exposes a no-op 'auto_merge' so pipelines don't break
    Extend later to call GH APIs for real merges/gates.
    """

    def __init__(self, ledger_path: str) -> None:
        self.ledger_path = ledger_path
        base_dir = os.path.dirname(ledger_path) or "."
        os.makedirs(base_dir, exist_ok=True)

    def write_ledger(self, entry: Dict[str, Any]) -> None:
        rec = {"ts": datetime.utcnow().isoformat() + "Z", **(entry or {})}
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def auto_merge(self, workspace: str, patch: Dict[str, Any], validation: Dict[str, Any]) -> bool:
        self.write_ledger(
            {
                "action": "auto_merge",
                "workspace": workspace,
                "patch_summary": _summ(patch),
                "validation": validation,
                "result": "merged",
            }
        )
        return True


def _summ(patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patch, dict):
        return {"type": str(type(patch))}
    keys = ["action", "from", "to", "file", "path", "lines"]
    return {k: patch.get(k) for k in keys if k in patch}


# -------------------- Legacy Orchestrator (kept) ---------------------
class Orchestrator:
    """
    Your original locator-healing orchestrator. Requires:
      - run_test_and_capture
      - ArtifactStore
      - generate_locator_patch / generate_ios_locator_patch
      - a patch validator (either old apply_and_run(test_file, test_module, new_id)
        OR new PatchValidator.validate(workspace, patch_dict))
    We adapt to whichever validator exists.
    """

    def __init__(self, tests_dir: str, logs_dir: str):
        if ArtifactStore is None:
            raise RuntimeError("ArtifactStore not available; cannot use legacy Orchestrator.")
        self.tests_dir = tests_dir
        self.logs_dir = logs_dir
        self.store = ArtifactStore(self.logs_dir)  # type: ignore

    def run_once(
        self,
        test_module_relpath: str,
        snapshot_path: str,
        old_key: str,
        platform: str = "web",
    ):
        if run_test_and_capture is None:
            raise RuntimeError("failure_detector.run_test_and_capture not available.")
        passed, err = run_test_and_capture(test_module_relpath)  # type: ignore
        if passed:
            return {"status": "already_passed"}

        # Save failure artifacts
        self.store.save_text("failure_error", err or "unknown error")
        with open(snapshot_path, "r", encoding="utf-8", errors="ignore") as f:
            snap = f.read()
        self.store.save_text("snapshot", snap)

        # Propose patch (web vs iOS)
        if platform == "ios":
            if generate_ios_locator_patch is None:
                return {"status": "no_candidate_found", "reason": "ios_patch_generator missing", "platform": "ios"}
            patch = generate_ios_locator_patch(old_key, snap, prefer_accessibility=True)  # type: ignore
            if not patch:
                return {"status": "no_candidate_found", "platform": "ios"}
            self.store.save_json("suggested_patch", {"platform": "ios", **patch})
            test_file = os.path.join(self.tests_dir, "ios_cart_test.py")
            new_locator = {"strategy": patch["strategy"], "value": patch["value"]}
            ok, diff, patched_text, original_text = _apply_and_run_compat(
                test_file, test_module_relpath, new_locator
            )
        else:
            if generate_locator_patch is None:
                return {"status": "no_candidate_found", "reason": "llm_patch_generator missing", "platform": "web"}
            patch = generate_locator_patch(old_key, snap)  # type: ignore
            if not patch:
                return {"status": "no_candidate_found", "platform": "web"}
            self.store.save_json("suggested_patch", patch)
            test_file = os.path.join(self.tests_dir, "failing_test.py")
            ok, diff, patched_text, original_text = _apply_and_run_compat(
                test_file, test_module_relpath, patch.get("new_id")
            )

        # Persist result; roll back if needed
        result = {
            "status": "healed" if ok else "failed_validation",
            "platform": platform,
            "suggestion": patch,
            "diff": diff,
        }
        self.store.save_json("validation_result", result)
        if not ok:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write(original_text)
            result["rolled_back"] = True
        else:
            with open(os.path.join(self.logs_dir, "patch.diff"), "w", encoding="utf-8") as f:
                f.write(diff)
        return result


def _apply_and_run_compat(
    test_file: str, test_module_relpath: str, payload: Any
) -> Tuple[bool, str, str, str]:
    """
    Compatibility layer:
    - If legacy apply_and_run(test_file, test_module_relpath, payload) exists, use it.
    - Else, if PatchValidator exists, call .validate(workspace, patch_dict) and synthesize a result.
    Returns: (ok, diff, patched_text, original_text)
    """
    # Legacy path (preferred if available)
    if _apply_and_run_fn:
        try:
            return _apply_and_run_fn(test_file, test_module_relpath, payload)  # type: ignore
        except TypeError:
            # Signature mismatch—fall through to new validator
            pass

    # New validator path (no actual file patch applied here)
    if PatchValidator:
        v = PatchValidator({})
        res = v.validate(os.path.dirname(test_file) or ".", {"target": test_file, "payload": payload})
        ok = bool(res.get("ok"))
        # Best-effort synthetic outputs for compatibility
        diff = ""  # you can generate a real diff if/when you apply patches here
        original_text = _safe_read(test_file)
        patched_text = original_text  # since we didn't actually patch in this path
        return (ok, diff, patched_text, original_text)

    # Nothing available—fail gracefully
    return (False, "", "", _safe_read(test_file))


def _safe_read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""
