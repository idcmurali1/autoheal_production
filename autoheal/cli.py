# autoheal/cli.py
from __future__ import annotations
import argparse
import json
import os
import subprocess

from .logger import get_logger
from .config import load_config
from .artifact_store import ArtifactStore

# LLM / RAG
from .prompt_builder import PromptBuilder
from .providers import get_llm
from .retriever import LocalRetriever
from .retriever_git import GitHistoryRetriever

# Patch application / validation
from .patch_strategies import find_and_replace_text
from .patch_validator import PatchValidator
from .ci_orchestrator import LocalCIOrchestrator

# Mappings utilities
from .mapping_updater import update_logical_name_across_modules
from .identifier_extractor import (
    extract_identifiers,                 # expects: (app_repo, platform, config_path) -> {'react_native': [...]} etc.
    rn_value_to_platform_locators,
    choose_logical_for_rn,
    choose_logical_generic,
)

log = get_logger(__name__)


def run(event: str, workspace: str, config_path: str):
    """
    Demo auto-heal pipeline for a CI event JSON.
    Adds RAG context from LocalRetriever; falls back cleanly if not available.
    """
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)

    event_data = json.load(open(event, "r"))
    logs_path = event_data.get("log_path", "")
    logs = open(logs_path, "r").read() if logs_path and os.path.exists(logs_path) else ""

    failure = {
        "test_name": event_data.get("test_name", "unknown_test"),
        "logs": logs,
        "dom_snapshot_path": event_data.get("dom_snapshot", ""),
        "broken_locator": event_data.get("broken_locator", "btnCheckout"),
        "workspace": event_data.get("workspace", workspace),
    }

    # --- RAG: Local vector context (best-effort) ---
    retrieved = []
    try:
        retriever = LocalRetriever(cfg.vectordb.base_path)
        retrieved = retriever.topk({"broken": failure.get("broken_locator", "")}, k=5)
    except Exception as e:
        log.info(f"RAG (LocalRetriever) failed: {e}")
        retrieved = []

    prompt = PromptBuilder().build(failure, retrieved)
    artifacts.put_json("prompt.json", prompt)

    llm = get_llm(
        cfg.llm.provider,
        cfg.llm.openai_api_key,
        cfg.llm.anthropic_api_key,
        cfg.llm.model,
        cfg.llm.temperature,
    )
    patch = llm.generate_patch(prompt)
    artifacts.put_json("patch.json", patch)

    validator = PatchValidator({})
    result = validator.validate(workspace, patch)
    artifacts.put_json("validation.json", result)

    orchestrator = LocalCIOrchestrator(cfg.logging.patch_ledger)
    if result.get("ok"):
        orchestrator.auto_merge(workspace, patch, result)
        log.info("Patch auto-merged (local).")
        print(
            json.dumps(
                {
                    "status": "success",
                    "message": "Auto-healed and merged",
                    "validation": result,
                },
                indent=2,
            )
        )
    else:
        orchestrator.write_ledger(
            {
                "status": "failed",
                "patch": patch,
                "validation": result,
                "workspace": workspace,
            }
        )
        raise SystemExit("Validation failed; patch not merged.")


def heal_text_rename(
    app_repo: str,
    tests_repo: str,
    old_text: str,
    new_text: str,
    branch: str,
    config_path: str,
    github_token: str = "",
):
    """
    LLM-assisted bulk text rename with RAG context:
      - Git history retriever (last 50 commits) to build prompt context
      - Apply deterministic find/replace across tests repo
      - Optionally open PR
    """
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)

    # --- RAG: Git history context (best-effort) ---
    retrieved = []
    try:
        retr = GitHistoryRetriever(cfg.vectordb.base_path)
        retr.ingest_commits(app_repo, 50)
        retrieved = retr.topk({"text": old_text}, k=10)
    except Exception as e:
        log.info(f"RAG (GitHistoryRetriever) failed: {e}")
        retrieved = []

    prompt = PromptBuilder().build(
        {
            "test_name": "text_rename",
            "logs": f"expected '{old_text}'",
            "dom_snapshot_path": "",
            "broken_locator": old_text,
        },
        retrieved,
    )
    llm = get_llm(
        cfg.llm.provider,
        cfg.llm.openai_api_key,
        cfg.llm.anthropic_api_key,
        cfg.llm.model,
        cfg.llm.temperature,
    )
    patch = llm.generate_patch(prompt)
    patch.update({"action": "text_rename", "from": old_text, "to": new_text})
    artifacts.put_json("patch_text_rename.json", patch)

    # Deterministic application in tests repo
    res = find_and_replace_text(tests_repo, old_text, new_text)
    artifacts.put_json("text_rename_result.json", res)

    # Best-effort: run tests if a common runner is present
    try:
        if os.path.exists(os.path.join(tests_repo, "package.json")):
            subprocess.run(["npm", "test", "--silent"], cwd=tests_repo, check=False)
        elif os.path.exists(os.path.join(tests_repo, "pytest.ini")) or os.path.exists(
            os.path.join(tests_repo, "tests")
        ):
            subprocess.run([os.sys.executable, "-m", "pytest", "-q"], cwd=tests_repo, check=False)
        elif os.path.exists(os.path.join(tests_repo, "gradlew")):
            subprocess.run(["./gradlew", "test"], cwd=tests_repo, check=False)
    except Exception as e:
        log.info(f"Test run failed: {e}")

    target_repo = "idcmurali1/Automation-test-framework-HOB"
    if github_token:
        try:
            subprocess.run(["git", "checkout", "-b", branch], cwd=tests_repo, check=False)
            subprocess.run(["git", "add", "-A"], cwd=tests_repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"Auto-heal: rename '{old_text}' -> '{new_text}'"],
                cwd=tests_repo,
                check=False,
            )
            subprocess.run(["git", "push", "-u", "origin", branch], cwd=tests_repo, check=True)

            from .github_client import GitHubClient

            gh = GitHubClient(github_token, target_repo)
            pr = gh.open_pr(
                title=f"Auto-heal: rename '{old_text}' -> '{new_text}'",
                head=branch,
                base="main",
                body="Automated patch",
            )
            artifacts.put_json("pull_request.json", pr)
            print(json.dumps({"status": "success", "pr": pr.get("html_url")}, indent=2))
        except Exception as e:
            print(
                json.dumps(
                    {
                        "status": "partial",
                        "message": "Applied rename locally; PR not opened",
                        "error": str(e),
                    },
                    indent=2,
                )
            )
    else:
        print(json.dumps({"status": "success", "message": "Applied rename locally (no PR)"}, indent=2))


def update_mappings_by_name(
    tests_repo: str,
    logical_name: str,
    android_id: str,
    ios_id: str,
    branch: str,
    config_path: str,
    github_token: str = "",
):
    """
    Deterministic updater: walk all modules and update identifier for a given logical name.
    """
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)

    res = update_logical_name_across_modules(
        tests_repo=tests_repo,
        logical_name=logical_name,
        new_android_identifier=android_id,
        new_ios_identifier=ios_id,
        include_locale_files=True,
    )
    artifacts.put_json("bulk_update_result.json", res)

    if res.get("updated", 0) == 0:
        print(json.dumps({"status": "noop", "message": "No files needed changes"}, indent=2))
        return

    if github_token:
        try:
            subprocess.run(["git", "checkout", "-b", branch], cwd=tests_repo, check=False)
            subprocess.run(["git", "add", "-A"], cwd=tests_repo, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"Auto-heal: update '{logical_name}' identifiers"],
                cwd=tests_repo,
                check=False,
            )
            subprocess.run(["git", "push", "-u", "origin", branch], cwd=tests_repo, check=True)

            from .github_client import GitHubClient

            gh = GitHubClient(github_token, "idcmurali1/Automation-test-framework-HOB")
            pr = gh.open_pr(
                title=f"Auto-heal: update identifiers for {logical_name}",
                head=branch,
                base="main",
                body="Bulk update across modules",
            )
            artifacts.put_json("pull_request.json", pr)
            print(json.dumps({"status": "success", "pr": pr.get("html_url")}, indent=2))
        except Exception as e:
            print(
                json.dumps(
                    {"status": "partial", "message": "Updated locally; PR not opened", "error": str(e)},
                    indent=2,
                )
            )
    else:
        print(json.dumps({"status": "success", "message": "Updated locally (no PR)"}, indent=2))


def update_mappings_from_app(
    app_repo: str,
    tests_repo: str,
    logical: str,                 # optional filter (keep for CLI compatibility)
    branch: str,
    config_path: str,
    github_token: str = "",
):
    """
    Scan app source (RN/iOS/Android) using config-driven source_files,
    map discovered identifiers to logical names from config.app (exact + regex + fuzzy),
    and update YAML mappings across modules. Opens one PR if changes exist.
    """
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)

    # Read platform + maps from config
    app_cfg = getattr(cfg, "app", {}) or {}
    platform       = app_cfg.get("platform", "react_native")

    # Exact maps + pattern lists (all optional)
    rn_map           = app_cfg.get("testid_to_logical", {})      # RN testID -> logical
    rn_patterns      = app_cfg.get("testid_patterns", [])        # list of {match, logical}
    ios_exact        = app_cfg.get("ios_to_logical", {})         # iOS id/name -> logical
    ios_patterns     = app_cfg.get("ios_patterns", [])           # list of {match, logical}
    android_exact    = app_cfg.get("android_to_logical", {})     # Android id/content-desc -> logical
    android_patterns = app_cfg.get("android_patterns", [])       # list of {match, logical}

    # Discover identifiers from the app (platform-aware)
    discovered = extract_identifiers(app_repo, platform, config_path)
    artifacts.put_json("identifiers_discovered.json", discovered)

    # Normalize discovered IDs list based on platform
    if platform == "react_native":
        ids = discovered.get("react_native", [])
    elif platform == "ios_native":
        ids = discovered.get("ios_native", [])
    elif platform == "android_native":
        ids = discovered.get("android_native", [])
    else:
        print(json.dumps({"status": "noop", "message": f"Unknown platform '{platform}'"}, indent=2))
        return

    updates = []  # list of (logical_name, android_identifier, ios_identifier)

    if platform == "react_native":
        for testid in ids:
            logical_name = choose_logical_for_rn(testid, rn_map, rn_patterns)
            if not logical_name:
                continue
            locs = rn_value_to_platform_locators(testid)
            updates.append((logical_name, locs["android"], locs["ios"]))

    elif platform == "ios_native":
        for ios_id in ids:
            logical_name = choose_logical_generic(ios_id, ios_exact, ios_patterns)
            if not logical_name:
                continue
            ios_locator = f"//*[@name='{ios_id}']"
            updates.append((logical_name, "", ios_locator))

    elif platform == "android_native":
        for aid in ids:
            logical_name = choose_logical_generic(aid, android_exact, android_patterns)
            if not logical_name:
                continue
            android_locator = (
                f"//*[@resource-id='{aid}'] | //*[@content-desc='{aid}']"
                if ":" in aid else f"//*[@content-desc='{aid}'] | //*[@resource-id='{aid}']"
            )
            updates.append((logical_name, android_locator, ""))

    # Optional: further restrict to a single logical passed via CLI
    if logical and logical.strip() and updates:
        updates = [u for u in updates if u[0] == logical.strip()]

    if not updates:
        print(json.dumps({"status": "noop", "message": "No mapped identifiers found to update"}, indent=2))
        return

    # Apply all updates
    total_changed = 0
    summary = {"attempted": len(updates), "updated_files": 0, "per_logical": []}
    for logical_name, android_id, ios_id in updates:
        res = update_logical_name_across_modules(
            tests_repo=tests_repo,
            logical_name=logical_name,
            new_android_identifier=android_id,
            new_ios_identifier=ios_id,
            include_locale_files=True,
        )
        total_changed += res.get("updated", 0)
        summary["updated_files"] += res.get("updated", 0)
        summary["per_logical"].append(
            {"logical": logical_name, "android": android_id, "ios": ios_id, "result": res}
        )

    artifacts.put_json("identifiers_update_summary.json", summary)

    if total_changed == 0:
        print(json.dumps({"status": "noop", "message": "No identifier change detected"}, indent=2))
        return

    # Open PR (single PR with all changes)
    if github_token:
        try:
            subprocess.run(["git", "checkout", "-b", branch], cwd=tests_repo, check=False)
            subprocess.run(["git", "add", "-A"], cwd=tests_repo, check=True)
            commit_msg = (
                f"Auto-heal: update identifiers for '{logical.strip()}' from app"
                if logical and logical.strip() else "Auto-heal: update identifiers from app sources"
            )
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=tests_repo, check=False)
            subprocess.run(["git", "push", "-u", "origin", branch], cwd=tests_repo, check=True)

            from .github_client import GitHubClient
            gh = GitHubClient(github_token, "idcmurali1/Automation-test-framework-HOB")
            pr = gh.open_pr(
                title=commit_msg, head=branch, base="main",
                body="Automated update based on app source changes"
            )
            artifacts.put_json("pull_request.json", pr)
            print(json.dumps({"status": "success", "pr": pr.get("html_url"), "changed": total_changed}, indent=2))
        except Exception as e:
            print(json.dumps(
                {"status": "partial", "message": "Updated locally; PR not opened", "error": str(e), "changed": total_changed},
                indent=2,
            ))
    else:
        print(json.dumps({"status": "success", "message": "Updated locally (no PR)", "changed": total_changed}, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Autoheal CLI (LLM + RAG + deterministic updaters)")
    sub = ap.add_subparsers(dest="cmd")

    # Demo CI pipeline
    r = sub.add_parser("run", help="Run the demo auto-heal pipeline on a CI event JSON")
    r.add_argument("--event", required=True, help="Path to CI event JSON")
    r.add_argument("--workspace", required=True, help="Workspace root")
    r.add_argument("--config", required=True, help="Path to config.yaml")

    # Text rename flow (LLM + RAG + deterministic file change)
    t = sub.add_parser("heal-text-rename", help="Rename text in tests repo and (optionally) open a PR")
    t.add_argument("--app_repo", required=True)
    t.add_argument("--tests_repo", required=True)
    t.add_argument("--old", required=True)
    t.add_argument("--new", required=True)
    t.add_argument("--branch", required=True)
    t.add_argument("--config", required=True)
    t.add_argument("--github_token", required=False)

    # Deterministic mapping updater by logical name
    u = sub.add_parser("update-mappings-by-name", help="Update a logical name across all module mappings")
    u.add_argument("--tests_repo", required=True)
    u.add_argument("--logical", required=True)
    u.add_argument("--android_id", required=False, default="")
    u.add_argument("--ios_id", required=False, default="")
    u.add_argument("--branch", required=True)
    u.add_argument("--config", required=True)
    u.add_argument("--github_token", required=False)

    # Extract IDs from app and update mappings (multi-platform, config-driven)
    v = sub.add_parser(
        "update-mappings-from-app",
        help="Extract identifiers from app repo then update mappings (opens PR if token provided)",
    )
    v.add_argument("--app_repo", required=True)
    v.add_argument("--tests_repo", required=True)
    v.add_argument("--logical", required=True)   # optional filter, keeps CLI compat
    v.add_argument("--branch", required=True)
    v.add_argument("--config", required=True)
    v.add_argument("--github_token", required=False)

    args = ap.parse_args()

    if args.cmd == "run":
        run(args.event, args.workspace, args.config)
    elif args.cmd == "heal-text-rename":
        heal_text_rename(
            args.app_repo,
            args.tests_repo,
            args.old,
            args.new,
            args.branch,
            args.config,
            args.github_token or "",
        )
    elif args.cmd == "update-mappings-by-name":
        update_mappings_by_name(
            args.tests_repo,
            args.logical,
            args.android_id,
            args.ios_id,
            args.branch,
            args.config,
            args.github_token or "",
        )
    elif args.cmd == "update-mappings-from-app":
        update_mappings_from_app(
            args.app_repo,
            args.tests_repo,
            args.logical,
            args.branch,
            args.config,
            args.github_token or "",
        )
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
