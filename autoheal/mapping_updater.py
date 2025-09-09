import os, yaml, glob
from typing import Dict, List, Optional

DEFAULT_FILES = [
    "mappings-android.yaml",
    "mappings-ios.yaml",
    "mappings-android-spanish.yaml",
    "mappings-ios-spanish.yaml",
]

def _load_yaml(path: str) -> dict:
    """Load YAML, normalizing tabs and returning a sentinel on parse errors."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # normalize tabs to spaces (common cause of failures in CI)
    if "\t" in text:
        text = text.replace("\t", "  ")
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        # Return a sentinel so callers can decide to skip this file safely
        return {"__PARSE_ERROR__": str(e)}

def _save_yaml(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=True)

def _platform_from_filename(filename: str) -> Optional[str]:
    fn = filename.lower()
    if "android" in fn: return "android"
    if "ios" in fn: return "ios"
    return None

def _update_mapping(doc: dict, platform_key: str, logical_name: str, new_identifier: str) -> bool:
    if platform_key not in doc or not isinstance(doc[platform_key], list):
        return False
    changed = False
    for item in doc[platform_key]:
        if isinstance(item, dict) and item.get("name") == logical_name:
            if item.get("identifier") != new_identifier:
                item["identifier"] = new_identifier
                changed = True
    return changed

def update_logical_name_across_modules(
    tests_repo: str,
    logical_name: str,
    new_android_identifier: Optional[str] = None,
    new_ios_identifier: Optional[str] = None,
    modules_root: str = "us/e2e-tests/modules",
    include_locale_files: bool = True,
    files_override: Optional[List[str]] = None,
) -> Dict:
    root = os.path.join(tests_repo, modules_root)
    if not os.path.isdir(root):
        return {"updated": 0, "files": [], "skipped_bad_yaml": [], "message": f"Modules dir not found: {root}"}

    filenames = files_override or DEFAULT_FILES
    if not include_locale_files:
        filenames = [f for f in filenames if "spanish" not in f]

    results = []
    skipped_bad_yaml = []
    updated_count = 0

    for module_dir in sorted(d for d in glob.glob(os.path.join(root, "*")) if os.path.isdir(d)):
        for fname in filenames:
            path = os.path.join(module_dir, fname)
            if not os.path.exists(path):
                continue

            platform = _platform_from_filename(fname)
            if platform == "android" and not new_android_identifier:
                continue
            if platform == "ios" and not new_ios_identifier:
                continue

            doc = _load_yaml(path)
            # Skip files that failed to parse; record the error
            if isinstance(doc, dict) and "__PARSE_ERROR__" in doc:
                skipped_bad_yaml.append({"file": path, "error": doc["__PARSE_ERROR__"]})
                results.append({"file": path, "platform": platform, "changed": False, "skipped": True})
                continue

            if platform == "android":
                changed = _update_mapping(doc, "android", logical_name, new_android_identifier)
            elif platform == "ios":
                changed = _update_mapping(doc, "ios", logical_name, new_ios_identifier)
            else:
                changed = False

            if changed:
                _save_yaml(path, doc)
                updated_count += 1
                results.append({"file": path, "platform": platform, "changed": True, "skipped": False})
            else:
                results.append({"file": path, "platform": platform, "changed": False, "skipped": False})

    return {
        "updated": updated_count,
        "files": results,
        "skipped_bad_yaml": skipped_bad_yaml,
        "logical_name": logical_name,
        "modules_root": root,
    }
