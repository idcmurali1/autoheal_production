import argparse, os, json, subprocess
from .logger import get_logger
from .config import load_config
from .artifact_store import ArtifactStore
from .prompt_builder import PromptBuilder
from .providers import get_llm
from .retriever import LocalRetriever
from .retriever_git import GitHistoryRetriever
from .patch_strategies import find_and_replace_text
from .patch_validator import PatchValidator
from .ci_orchestrator import LocalCIOrchestrator
from .mapping_updater import update_logical_name_across_modules
from .identifier_extractor import extract_menu_settings_identifiers

log = get_logger(__name__)

def run(event: str, workspace: str, config_path: str):
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)
    event_data = json.load(open(event, "r"))
    logs = open(event_data["log_path"], "r").read() if os.path.exists(event_data["log_path"]) else ""
    failure = {
        "test_name": event_data.get("test_name", "unknown_test"),
        "logs": logs,
        "dom_snapshot_path": event_data.get("dom_snapshot",""),
        "broken_locator": "btnCheckout",
        "workspace": event_data.get("workspace", workspace),
    }
    retriever = LocalRetriever(cfg.vectordb.base_path)
    retrieved = retriever.topk({"broken": failure.get("broken_locator")}, k=3)
    prompt = PromptBuilder().build(failure, retrieved)
    llm = get_llm(cfg.llm.provider, cfg.llm.openai_api_key, cfg.llm.anthropic_api_key, cfg.llm.model, cfg.llm.temperature)
    patch = llm.generate_patch(prompt)
    artifacts.put_json("prompt.json", prompt)
    artifacts.put_json("patch.json", patch)
    validator = PatchValidator({})
    result = validator.validate(workspace, patch)
    artifacts.put_json("validation.json", result)
    orchestrator = LocalCIOrchestrator(cfg.logging.patch_ledger)
    if result.get("ok"):
        orchestrator.auto_merge(workspace, patch, result)
        log.info("Patch auto-merged (local).")
        print(json.dumps({"status":"success","message":"Auto-healed and merged","validation":result}, indent=2))
    else:
        orchestrator.write_ledger({"status":"failed","patch":patch,"validation":result,"workspace":workspace})
        raise SystemExit("Validation failed; patch not merged.")

def heal_text_rename(app_repo, tests_repo, old_text, new_text, branch, config_path, github_token=''):
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)
    retr = GitHistoryRetriever(cfg.vectordb.base_path)
    try:
        retr.ingest_commits(app_repo, 50)
        retrieved = retr.topk({"text": old_text}, 10)
    except Exception as e:
        log.info(f'RAG failed: {e}'); retrieved=[]
    prompt = PromptBuilder().build(
        {"test_name":"text_rename","logs":f"expected '{old_text}'","dom_snapshot_path":"","broken_locator":old_text},
        retrieved
    )
    llm = get_llm(cfg.llm.provider, cfg.llm.openai_api_key, cfg.llm.anthropic_api_key, cfg.llm.model, cfg.llm.temperature)
    patch = llm.generate_patch(prompt); patch.update({"action":"text_rename","from":old_text,"to":new_text})
    artifacts.put_json("patch_text_rename.json", patch)
    res = find_and_replace_text(tests_repo, old_text, new_text)
    artifacts.put_json("text_rename_result.json", res)
    try:
        if os.path.exists(os.path.join(tests_repo, "package.json")):
            subprocess.run(["npm","test","--silent"], cwd=tests_repo, check=False)
        elif os.path.exists(os.path.join(tests_repo, "pytest.ini")) or os.path.exists(os.path.join(tests_repo, "tests")):
            subprocess.run([os.sys.executable,"-m","pytest","-q"], cwd=tests_repo, check=False)
        elif os.path.exists(os.path.join(tests_repo, "gradlew")):
            subprocess.run(["./gradlew","test"], cwd=tests_repo, check=False)
    except Exception as e:
        log.info(f"Test run failed: {e}")
    target_repo = "idcmurali1/Automation-test-framework-HOB"
    if github_token:
        try:
            subprocess.run(["git","checkout","-b",branch], cwd=tests_repo, check=False)
            subprocess.run(["git","add","-A"], cwd=tests_repo, check=True)
            subprocess.run(["git","commit","-m", f"Auto-heal: rename '{old_text}' -> '{new_text}'"], cwd=tests_repo, check=False)
            subprocess.run(["git","push","-u","origin", branch], cwd=tests_repo, check=True)
            from .github_client import GitHubClient
            gh = GitHubClient(github_token, target_repo)
            pr = gh.open_pr(title=f"Auto-heal: rename '{old_text}' -> '{new_text}'", head=branch, base="main", body="Automated patch")
            artifacts.put_json("pull_request.json", pr)
            print(json.dumps({"status":"success","pr":pr.get("html_url")}, indent=2))
        except Exception as e:
            print(json.dumps({"status":"partial","message":"Applied rename locally; PR not opened","error":str(e)}, indent=2))
    else:
        print(json.dumps({"status":"success","message":"Applied rename locally (no PR)"}, indent=2))

def update_mappings_by_name(tests_repo: str, logical_name: str, android_id: str, ios_id: str, branch: str, config_path: str, github_token: str = ""):
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
    if res.get("updated",0) == 0:
        print(json.dumps({"status":"noop","message":"No files needed changes"}, indent=2))
        return
    if github_token:
        try:
            subprocess.run(["git","checkout","-b",branch], cwd=tests_repo, check=False)
            subprocess.run(["git","add","-A"], cwd=tests_repo, check=True)
            subprocess.run(["git","commit","-m", f"Auto-heal: update '{logical_name}' identifiers"], cwd=tests_repo, check=False)
            subprocess.run(["git","push","-u","origin",branch], cwd=tests_repo, check=True)
            from .github_client import GitHubClient
            gh = GitHubClient(github_token, "idcmurali1/Automation-test-framework-HOB")
            pr = gh.open_pr(title=f"Auto-heal: update identifiers for {logical_name}", head=branch, base="main", body="Bulk update across modules")
            artifacts.put_json("pull_request.json", pr)
            print(json.dumps({"status":"success","pr":pr.get('html_url')}, indent=2))
        except Exception as e:
            print(json.dumps({"status":"partial","message":"Updated locally; PR not opened","error":str(e)}, indent=2))
    else:
        print(json.dumps({"status":"success","message":"Updated locally (no PR)"}, indent=2))

def update_mappings_from_app(app_repo: str, tests_repo: str, logical_name: str, branch: str, config_path: str, github_token: str = ""):
    cfg = load_config(config_path)
    artifacts = ArtifactStore(cfg.artifact_store.path)
    ids = extract_menu_settings_identifiers(app_repo)
    artifacts.put_json("identifiers_extracted.json", ids)
    android_id = ids.get("android") or ""
    ios_id = ids.get("ios") or ""
    res = update_logical_name_across_modules(
        tests_repo=tests_repo,
        logical_name=logical_name,
        new_android_identifier=android_id,
        new_ios_identifier=ios_id,
        include_locale_files=True,
    )
    artifacts.put_json("identifiers_update_result.json", res)
    if res.get("updated",0) == 0:
        print(json.dumps({"status":"noop","message":"No identifier change detected"}, indent=2))
        return
    if github_token:
        try:
            subprocess.run(["git","checkout","-b",branch], cwd=tests_repo, check=False)
            subprocess.run(["git","add","-A"], cwd=tests_repo, check=True)
            subprocess.run(["git","commit","-m", f"Auto-heal: update '{logical_name}' identifiers (from app)"], cwd=tests_repo, check=False)
            subprocess.run(["git","push","-u","origin",branch], cwd=tests_repo, check=True)
            from .github_client import GitHubClient
            gh = GitHubClient(github_token, "idcmurali1/Automation-test-framework-HOB")
            pr = gh.open_pr(title=f"Auto-heal: update identifiers for {logical_name} (from app)", head=branch, base="main", body="Automated update based on app source changes")
            artifacts.put_json("pull_request.json", pr)
            print(json.dumps({"status":"success","pr":pr.get('html_url')}, indent=2))
        except Exception as e:
            print(json.dumps({"status":"partial","message":"Updated locally; PR not opened","error":str(e)}, indent=2))
    else:
        print(json.dumps({"status":"success","message":"Updated locally (no PR)"}, indent=2))

def main():
    ap = argparse.ArgumentParser(description="Autoheal CLI (complete)")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="Run the demo auto-heal pipeline on a CI event")
    r.add_argument("--event", required=True, help="Path to CI event JSON")
    r.add_argument("--workspace", required=True, help="Workspace root")
    r.add_argument("--config", required=True, help="Path to config.yaml")

    t = sub.add_parser("heal-text-rename", help="Rename text in tests repo and open a PR")
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

    v = sub.add_parser("update-mappings-from-app", help="Extract identifiers from app repo then update mappings")
    v.add_argument("--app_repo", required=True)
    v.add_argument("--tests_repo", required=True)
    v.add_argument("--logical", required=True)
    v.add_argument("--branch", required=True)
    v.add_argument("--config", required=True)
    v.add_argument("--github_token", required=False)

    args = ap.parse_args()
    if args.cmd == "run":
        run(args.event, args.workspace, args.config)
    elif args.cmd == "heal-text-rename":
        heal_text_rename(args.app_repo, args.tests_repo, args.old, args.new, args.branch, args.config, args.github_token or "")
    elif args.cmd == "update-mappings-by-name":
        update_mappings_by_name(args.tests_repo, args.logical, args.android_id, args.ios_id, args.branch, args.config, args.github_token or "")
    elif args.cmd == "update-mappings-from-app":
        update_mappings_from_app(args.app_repo, args.tests_repo, args.logical, args.branch, args.config, args.github_token or "")
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
