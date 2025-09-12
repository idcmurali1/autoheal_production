# autoheal/cli.py
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime

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

# Identifier discovery + mapping helpers
from .identifier_extractor import (
    extract_identifiers,          # (app_repo, platform, config_path) -> {'react_native':[...]} etc.
    rn_value_to_platform_locators,
    choose_logical_for_rn,        # exact → regex patterns → fuzzy
    choose_logical_generic,
)

log = get_logger(__name__)

# --- add near the top of cli.py (after imports) ---

def _field(obj, key, default):
    """Return obj[key] if obj is dict, getattr(obj, key, default) if not."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def _resolve_env_template(val: str, fallback_env: str, default: str = "") -> str:
    """
    Resolve strings like '${OPENAI_API_KEY:-}' or '${LLM_PROVIDER:-openai}' against the environment.
    If 'val' isn't a template, return it unchanged.
    """
    import re, os
    if not isinstance(val, str):
        return val
    m = re.fullmatch(r"\$\{\s*([A-Z0-9_]+)\s*:-\s*([^}]*)\s*\}", val)
    if not m:
        return val
    env_name, templ_default = m.group(1), m.group(2)
    return os.getenv(env_name, templ_default if templ_default is not None else default)

# ------------------------
# LLM helper for identifier mapping
# ------------------------
def _call_llm_for_mappings(llm, artifacts: ArtifactStore, platform: str, identifiers: list[str], context: dict):
    """
    Call the LLM to propose logical names and locators for a list of UNMAPPED identifiers.
    We print before/after and persist request/response artifacts for debugging.
    Expected suggestion shape:
      {"identifier":"...", "logical":"...", "android":"...", "ios":"..."}
    """
    print(f"[DEBUG] Calling LLM for {len(identifiers)} unmapped identifiers:", identifiers)

    llm_req = {
        "task": "map_identifiers",
        "platform": platform,
        "identifiers": identifiers,
        "context": context,  # include prior maps/patterns to ground the model
    }
    artifacts.put_json("llm_request_identifiers.json", llm_req)

    # We reuse generate_patch as a generic "structured response" entry point.
    resp = llm.generate_patch({
        "instruction": "Suggest logical names and stable platform locators for the given identifiers.",
        **llm_req
    })

    print("[DEBUG] LLM response:", resp)
    artifacts.put_json("llm_response_identifiers.json", resp)

    suggestions = []
    if isinstance(resp, dict) and isinstance(resp.get("suggestions"), list):
        suggestions = resp["suggestions"]
    elif isinstance(resp, list):
        suggestions = resp
    return suggestions


# ------------------------
# Observability helpers
# ------------------------
def _dump_vectordb_manifest(base_path: str, artifacts: ArtifactStore, name: str):
    """
    Walk the local vector index folder and store a tiny manifest:
    - relative path
    - size bytes
    - modified time (ISO 8601)
    """
    try:
        if not base_path or not os.path.exists(base_path):
            artifacts.put_json(name, {
                "base_path": base_path,
                "exists": False,
                "files": [],
                "total_size_bytes": 0,
                "count": 0,
                "message": "Vector DB path not found (this is OK if RAG is optional)."
            })
            return

        files = []
        total = 0
        for root, _, fns in os.walk(base_path):
            for fn in fns:
                full = os.path.join(root, fn)
                try:
                    st = os.stat(full)
                    rel = os.path.relpath(full, base_path)
                    files.append({
                        "path": rel,
                        "size_bytes": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime).isoformat()
                    })
                    total += st.st_size
                except Exception as e:
                    files.append({"path": fn, "error": str(e)})

        artifacts.put_json(name, {
            "base_path": base_path,
            "exists": True,
            "count": len(files),
            "total_size_bytes": total,
            "files": files,
        })
    except Exception as e:
        log.info(f"Vector DB manifest generation failed: {e}")


def _write_llm_info_artifact(artifacts: ArtifactStore, provider: str, model: str, temperature: float):
    """Persist minimal LLM info (no secrets)."""
    try:
        payload = {
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "ts": datetime.utcnow().isoformat() + "Z"
        }
        artifacts.put_json("llm_info.json", payload)
    except Exception as e:
        log.info(f"Writing llm_info.json failed: {e}")


# ------------------------
# Main commands
# ------------------------
def run(event: str, workspace: str, config_path: str):
    """Demo auto-heal pipeline for a CI event JSON (kept as-is, with optional RAG)."""
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

    # Always capture a snapshot of the local vector DB layout for debugging
    _dump_vectordb_manifest(cfg.vectordb.base_path, artifacts, name="rag_index_manifest_run.json")

    prompt = PromptBuilder().build(failure, retrieved)
    artifacts.put_json("prompt.json", prompt)

    # --- LLM: log which backend/model we are using (no keys) ---
    log.info(f"LLM provider={cfg.llm.provider} model={cfg.llm.model}")
    _write_llm_info_artifact(artifacts, cfg.llm.provider, cfg.llm.model, cfg.llm.temperature)

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
        print(json.dumps(
            {"status": "success", "message": "Auto-healed and merged", "validation": result},
            indent=2
        ))
    else:
        orchestrator.write_ledger(
            {"status": "failed", "patch": patch, "validation": result, "workspace": workspace}
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
    """LLM-assisted bulk text rename with Git-history RAG."""
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)

    retrieved = []
    try:
        retr = GitHistoryRetriever(cfg.vectordb.base_path)
        retr.ingest_commits(app_repo, 50)
        retrieved = retr.topk({"text": old_text}, k=10)
    except Exception as e:
        log.info(f"RAG (GitHistoryRetriever) failed: {e}")
        retrieved = []

    # Vector DB manifest after Git ingest
    _dump_vectordb_manifest(cfg.vectordb.base_path, artifacts, name="rag_index_manifest_text_rename.json")

    prompt = PromptBuilder().build(
        {
            "test_name": "text_rename",
            "logs": f"expected '{old_text}'",
            "dom_snapshot_path": "",
            "broken_locator": old_text,
        },
        retrieved,
    )

    # LLM usage log/artifact
    log.info(f"LLM provider={cfg.llm.provider} model={cfg.llm.model}")
    _write_llm_info_artifact(artifacts, cfg.llm.provider, cfg.llm.model, cfg.llm.temperature)

    llm = get_llm(
        cfg.llm.provider, cfg.llm.openai_api_key, cfg.llm.anthropic_api_key, cfg.llm.model, cfg.llm.temperature
    )
    patch = llm.generate_patch(prompt)
    patch.update({"action": "text_rename", "from": old_text, "to": new_text})
    artifacts.put_json("patch_text_rename.json", patch)

    # Deterministic apply in tests repo
    res = find_and_replace_text(tests_repo, old_text, new_text)
    artifacts.put_json("text_rename_result.json", res)

    # Best-effort local test run
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
            print(json.dumps(
                {"status": "partial", "message": "Applied rename locally; PR not opened", "error": str(e)},
                indent=2
            ))
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
    """Deterministic updater: update identifier for a given logical name."""
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
            print(json.dumps(
                {"status": "partial", "message": "Updated locally; PR not opened", "error": str(e)},
                indent=2
            ))
    else:
        print(json.dumps({"status": "success", "message": "Updated locally (no PR)"}, indent=2))


def update_mappings_from_app(
    app_repo: str,
    tests_repo: str,
    logical: str,                 # optional filter
    branch: str,
    config_path: str,
    github_token: str = "",
):
    """
    Scan app source (RN/iOS/Android) using config-driven source_files,
    ask the LLM for any unmapped identifiers, merge with rule-based mappings,
    then update YAML mappings across modules. Opens one PR if changes exist.
    Produces the following artifacts at ./artifacts:

      - config_app_snapshot.json
      - identifiers_discovered.json
      - identifiers_discovered_runtime.json
      - llm_request_identifiers.json
      - llm_response_identifiers.json
      - identifiers_update_plan.json
      - identifiers_update_summary.json
      - rag_index_manifest_update_from_app.json
      - pull_request.json (PR data or error)
      - llm_info.json
    """
    import shutil
    import json as _json
    from datetime import datetime

    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)

    # ---- Snapshot of the relevant app config for traceability
    app_cfg = getattr(cfg, "app", None) or {}
    platform = app_cfg.get("platform", "react_native")
    rn_map        = app_cfg.get("testid_to_logical", {}) or {}
    rn_patterns   = app_cfg.get("testid_patterns", []) or []
    ios_map       = app_cfg.get("ios_to_logical", {}) or {}
    android_map   = app_cfg.get("android_to_logical", {}) or {}

    artifacts.put_json("config_app_snapshot.json", {
        "platform": platform,
        "testid_to_logical": rn_map,
        "testid_patterns": rn_patterns,
        "ios_to_logical": ios_map,
        "android_to_logical": android_map,
        "ts": datetime.utcnow().isoformat() + "Z",
    })

    # ---- Identifier discovery
    discovered = extract_identifiers(app_repo, platform, config_path)
    artifacts.put_json("identifiers_discovered.json", discovered)

    # Normalize discovered IDs list by platform
    if platform == "react_native":
        ids = discovered.get("react_native", [])
    elif platform == "ios_native":
        ids = discovered.get("ios_native", [])
    elif platform == "android_native":
        ids = discovered.get("android_native", [])
    else:
        print(_json.dumps({"status": "noop", "message": f"Unknown platform '{platform}'"}, indent=2))
        return

    # ---- DEBUG: record what the CLI is seeing before mapping
    artifacts.put_json("identifiers_discovered_runtime.json", {
        "platform": platform,
        "ids": ids,
        "rn_map_keys": list(rn_map.keys()),
        "rn_patterns": rn_patterns,
        "ios_map_keys": list(ios_map.keys()) if isinstance(ios_map, dict) else [],
        "android_map_keys": list(android_map.keys()) if isinstance(android_map, dict) else [],
    })

    # ---- Rule-based mapping (deterministic)
    updates_rb = []  # list of (logical_name, android_identifier, ios_identifier)
    rule_mapped = set()

    if platform == "react_native":
        for testid in ids:
            ln = choose_logical_for_rn(testid, rn_map, rn_patterns)
            if not ln:
                continue
            locs = rn_value_to_platform_locators(testid)
            updates_rb.append((ln, locs["android"], locs["ios"]))
            rule_mapped.add(testid)

    elif platform == "ios_native":
        for ios_id in ids:
            ln = choose_logical_generic(ios_id, ios_map, app_cfg.get("ios_patterns", []))
            if ln:
                updates_rb.append((ln, "", f"//*[@name='{ios_id}']"))
                rule_mapped.add(ios_id)

    elif platform == "android_native":
        for aid in ids:
            ln = choose_logical_generic(aid, android_map, app_cfg.get("android_patterns", []))
            if ln:
                android_locator = (
                    f"//*[@resource-id='{aid}'] | //*[@content-desc='{aid}']"
                    if ":" in aid else
                    f"//*[@content-desc='{aid}'] | //*[@resource-id='{aid}']"
                )
                updates_rb.append((ln, android_locator, ""))
                rule_mapped.add(aid)

    # ---- LLM for any still-unmapped identifiers
    unmapped = [i for i in ids if i not in rule_mapped]

    llm_suggestions = []
    if unmapped:
        # LLM usage log/artifact
        log.info(f"LLM provider={cfg.llm.provider} model={cfg.llm.model}")
        _write_llm_info_artifact(artifacts, cfg.llm.provider, cfg.llm.model, cfg.llm.temperature)

        llm = get_llm(
            cfg.llm.provider,
            cfg.llm.openai_api_key,
            cfg.llm.anthropic_api_key,
            cfg.llm.model,
            cfg.llm.temperature,
        )

        # Call and capture artifacts
        llm_req = {
            "task": "map_identifiers",
            "platform": platform,
            "identifiers": unmapped,
            "context": {
                "rn_map": rn_map, "rn_patterns": rn_patterns,
                "ios_map": ios_map, "android_map": android_map,
            },
        }
        artifacts.put_json("llm_request_identifiers.json", llm_req)
        resp = llm.generate_patch({
            "instruction": "Suggest logical names and stable platform locators for the given identifiers.",
            **llm_req
        })
        artifacts.put_json("llm_response_identifiers.json", resp)

        if isinstance(resp, dict) and isinstance(resp.get("suggestions"), list):
            llm_suggestions = resp["suggestions"]
        elif isinstance(resp, list):
            llm_suggestions = resp
        else:
            llm_suggestions = []

    # ---- Merge rule-based + LLM suggestions into a single update list
    updates = list(updates_rb)

    # Normalize LLM suggestions into (logical, android, ios)
    seen = {(u[0], u[1], u[2]) for u in updates}  # de-dupe on the full triple
    for s in llm_suggestions:
        ident = s.get("identifier", "")
        ln    = s.get("logical", "")
        andr  = s.get("android", "") or ""
        ios   = s.get("ios", "") or ""
        if not ln:
            continue
        tpl = (ln, andr, ios)
        if tpl not in seen:
            updates.append(tpl)
            seen.add(tpl)

    # Optional: filter to a single logical if provided
    if logical and updates:
        updates = [u for u in updates if u[0] == logical]

    # ---- Plan/debug artifacts (what we intend to change)
    artifacts.put_json("identifiers_update_plan.json", {
        "attempted_count": len(updates),
        "planned_updates": [{"logical": u[0], "android": u[1], "ios": u[2]} for u in updates],
    })

    # Also snapshot the vector index folder for transparency
    _dump_vectordb_manifest(cfg.vectordb.base_path, artifacts, name="rag_index_manifest_update_from_app.json")

    if not updates:
        print(_json.dumps({"status": "noop", "message": "No mapped identifiers found to update"}, indent=2))
        return

    # ---- Apply updates across test repo
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
        print(_json.dumps({"status": "noop", "message": "No identifier change detected"}, indent=2))
        return

    # ---- Single PR with all changes
    if github_token:
        try:
            subprocess.run(["git", "checkout", "-b", branch], cwd=tests_repo, check=False)
            subprocess.run(["git", "add", "-A"], cwd=tests_repo, check=True)
            commit_msg = (
                f"Auto-heal: update identifiers for '{logical}' from app"
                if logical else "Auto-heal: update identifiers from app sources"
            )
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=tests_repo, check=False)
            subprocess.run(["git", "push", "-u", "origin", branch], cwd=tests_repo, check=True)

            # Try API client first
            pr_url = None
            try:
                from .github_client import GitHubClient
                gh = GitHubClient(github_token, "idcmurali1/Automation-test-framework-HOB")
                pr = gh.open_pr(title=commit_msg, head=branch, base="main",
                                body="Automated update based on app source changes")
                artifacts.put_json("pull_request.json", pr)
                pr_url = (pr or {}).get("html_url")
            except Exception as e:
                artifacts.put_json("pull_request.json", {"error": str(e)})

            # Fallback: gh CLI if API returned error or no URL
            if not pr_url and shutil.which("gh"):
                env = os.environ.copy()
                env["GH_TOKEN"] = github_token
                cmd = [
                    "gh", "pr", "create",
                    "--title", commit_msg,
                    "--body", "Automated update based on app source changes",
                    "--base", "main",
                    "--head", branch,
                ]
                r = subprocess.run(cmd, cwd=tests_repo, env=env, capture_output=True, text=True, check=False)
                # gh prints URL on success
                line = (r.stdout.strip() or r.stderr.strip())
                if line:
                    pr_url = line.splitlines()[-1].strip()
                    artifacts.put_json("pull_request.json", {"html_url": pr_url, "via": "gh"})

            print(_json.dumps({"status": "success", "pr": pr_url, "changed": total_changed}, indent=2))

        except Exception as e:
            print(_json.dumps({"status":"partial","message":"Updated locally; PR not opened",
                               "error":str(e), "changed": total_changed}, indent=2))
    else:
        print(_json.dumps({"status":"success","message":"Updated locally (no PR)",
                           "changed": total_changed}, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Autoheal CLI (LLM + RAG + deterministic updaters)")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="Run the demo auto-heal pipeline on a CI event JSON")
    r.add_argument("--event", required=True)
    r.add_argument("--workspace", required=True)
    r.add_argument("--config", required=True)

    t = sub.add_parser("heal-text-rename", help="Rename text in tests repo and (optionally) open a PR")
    t.add_argument("--app_repo", required=True)
    t.add_argument("--tests_repo", required=True)
    t.add_argument("--old", required=True)
    t.add_argument("--new", required=True)
    t.add_argument("--branch", required=True)
    t.add_argument("--config", required=True)
    t.add_argument("--github_token", required=False)

    u = sub.add_parser("update-mappings-by-name", help="Update a logical name across all module mappings")
    u.add_argument("--tests_repo", required=True)
    u.add_argument("--logical", required=True)
    u.add_argument("--android_id", required=False, default="")
    u.add_argument("--ios_id", required=False, default="")
    u.add_argument("--branch", required=True)
    u.add_argument("--config", required=True)
    u.add_argument("--github_token", required=False)

    v = sub.add_parser(
        "update-mappings-from-app",
        help="Extract identifiers from app repo then update mappings (opens PR if token provided)",
    )
    v.add_argument("--app_repo", required=True)
    v.add_argument("--tests_repo", required=True)
    v.add_argument("--logical", required=False, default="")  # optional filter
    v.add_argument("--branch", required=True)
    v.add_argument("--config", required=True)
    v.add_argument("--github_token", required=False)

    args = ap.parse_args()
    if args.cmd == "run":
        run(args.event, args.workspace, args.config)
    elif args.cmd == "heal-text-rename":
        heal_text_rename(
            args.app_repo, args.tests_repo, args.old, args.new, args.branch, args.config, args.github_token or ""
        )
    elif args.cmd == "update-mappings-by-name":
        update_mappings_by_name(
            args.tests_repo, args.logical, args.android_id, args.ios_id, args.branch, args.config, args.github_token or ""
        )
    elif args.cmd == "update-mappings-from-app":
        update_mappings_from_app(
            args.app_repo, args.tests_repo, args.logical, args.branch, args.config, args.github_token or ""
        )
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
