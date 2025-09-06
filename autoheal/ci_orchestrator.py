
import os
from .failure_detector import run_test_and_capture
from .artifact_store import ArtifactStore
from .llm_patch_generator import generate_locator_patch
from .patch_validator import apply_and_run
from .ios_patch_generator import generate_ios_locator_patch

class Orchestrator:
    def __init__(self, tests_dir: str, logs_dir: str):
        self.tests_dir = tests_dir
        self.logs_dir = logs_dir
        self.store = ArtifactStore(self.logs_dir)

    def run_once(self, test_module_relpath: str, snapshot_path: str, old_key: str, platform: str = "web"):
        passed, err = run_test_and_capture(test_module_relpath)
        if passed:
            return {"status": "already_passed"}

        self.store.save_text("failure_error", err or "unknown error")
        with open(snapshot_path, "r") as f:
            snap = f.read()
        self.store.save_text("snapshot", snap)

        if platform == "ios":
            patch = generate_ios_locator_patch(old_key, snap, prefer_accessibility=True)
            if not patch:
                return {"status": "no_candidate_found", "platform": "ios"}
            self.store.save_json("suggested_patch", {"platform":"ios", **patch})
            test_file = os.path.join(self.tests_dir, "ios_cart_test.py")
            new_locator = {"strategy": patch["strategy"], "value": patch["value"]}
            ok, diff, patched_text, original_text = apply_and_run(test_file, test_module_relpath, new_locator)
        else:
            patch = generate_locator_patch(old_key, snap)
            if not patch:
                return {"status": "no_candidate_found", "platform": "web"}
            self.store.save_json("suggested_patch", patch)
            test_file = os.path.join(self.tests_dir, "failing_test.py")
            ok, diff, patched_text, original_text = apply_and_run(test_file, test_module_relpath, patch["new_id"])

        result = {"status": "healed" if ok else "failed_validation", "platform": platform, "suggestion": patch, "diff": diff}
        self.store.save_json("validation_result", result)
        if not ok:
            with open(test_file, "w") as f:
                f.write(original_text)
            result["rolled_back"] = True
        else:
            with open(os.path.join(self.logs_dir, "patch.diff"), "w") as f:
                f.write(diff)
        return result
