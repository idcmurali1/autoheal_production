import os, glob
from typing import Dict, List, Optional

# Prefer ruamel.yaml (round-trip, preserves formatting)
try:
    from ruamel.yaml import YAML
    from ruamel.yaml.scalarstring import SingleQuotedScalarString as SQ
    _RUAMEL = True
    _yaml_rt = YAML()
    _yaml_rt.preserve_quotes = True
    _yaml_rt.width = 100000          # never wrap long XPath strings
    _yaml_rt.indent(mapping=2, sequence=2, offset=0)
except Exception:
    _RUAMEL = False
    import yaml

DEFAULT_FILES = [
    "mappings-android.yaml",
    "mappings-ios.yaml",
    "mappings-android-spanish.yaml",
    "mappings-ios-spanish.yaml",
]

def _load_yaml(path: str):
    """Load YAML, being tolerant of tabs by converting to spaces first."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if "\t" in text:
        text = text.replace("\t", "  ")

    if _RUAMEL:
        from io import StringIO
        return _yaml_rt.load(StringIO(text))
    else:
        return yaml.safe_load(text) or {}

def _needs_quote(val: str) -> bool:
    """Heuristic: values that should always be single-quoted to avoid YAML parse issues."""
    if not isinstance(val, str):
        return False
    # Colons (package:id), pipes, hashes, starting punctuation or xpath
    risky_chars = [":", "|", "#"]
    if any(c in val for c in risky_chars):
        return True
    if val.startswith(("/", "[", "(", "*", "@")):
        return True
    if "@" in val or "$" in val:  # xpath vars / templates
        return True
    return False

def _quote_identifier(value):
    """Return a scalar that will be emitted single-quoted (ruamel) or a plain string (PyYAML)."""
    if _RUAMEL:
        # Always force single quotes for identifiers to be safe
        return SQ(value)
    else:
        # PyYAML: quoting is implicit, but we avoid wrapping by setting width huge in _save_yaml
        return value

def _save_yaml(path: str, obj) -> None:
    """Dump YAML without wrapping long lines, keeping formatting stable."""
    if _RUAMEL:
        with open(path, "w", encoding="utf-8") as f:
            _yaml_rt.dump(obj, f)
    else:
        # PyYAML fallback: avoid wrapping; keep unicode; stable key order
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                obj,
                f,
                sort_keys=False,
                allow_unicode=True,
                width=100000,
                default_flow_style=False,
            )

def _platform_from_filename(filename: str) -> Optional[str]:
    fn = filename.lower()
    if "android" in fn: return "android"
    if "ios" in fn: return "ios"
    return None

def _update_mapping(doc: dict, platform_key: str, logical_name: str, new_identifier: str) -> bool:
    """
    Update the mapping with a new identifier. Ensures the identifier is quoted and stays on one line.
    """
    if platform_key not in doc or not isinstance(doc[platform_key], list):
        return False

    changed = False
    for item in doc[platform_key]:
        if isinstance(item, dict) and item.get("name") == logical_name:
            current = item.get("identifier")
            if current != new_identifier:
                # Ensure single-quoted scalars for safety
                new_val = _quote_identifier(new_identifier) if _needs_quote(new_identifier) else new_identifier
                item["identifier"] = new_val
                changed = True
            else:
                # Even if equal, normalize quoting (optional)
                if _RUAMEL and isinstance(current, str) and _needs_quote(current):
                    item["identifier"] = SQ(current)
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
        return {"updated": 0, "files": [], "message": f"Modules dir not found: {root}"}

    filenames = files_override or DEFAULT_FILES
    if not include_locale_files:
        filenames = [f for f in filenames if "spanish" not in f]

    results = []
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

            try:
                doc = _load_yaml(path)

                if platform == "android":
                    changed = _update_mapping(doc, "android", logical_name, new_android_identifier)
                elif platform == "ios":
                    changed = _update_mapping(doc, "ios", logical_name, new_ios_identifier)
                else:
                    changed = False

                if changed:
                    _save_yaml(path, doc)
                    updated_count += 1
                    results.append({"file": path, "platform": platform, "changed": True})
                else:
                    results.append({"file": path, "platform": platform, "changed": False})

            except Exception as e:
                # Log the file and continue so one bad YAML doesn't block others
                results.append({"file": path, "platform": platform, "changed": False, "error": str(e)})

    return {"updated": updated_count, "files": results, "logical_name": logical_name, "modules_root": root}
