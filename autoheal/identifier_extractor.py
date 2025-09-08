# autoheal/identifier_extractor.py
import os, re, yaml
from typing import Dict, Any


def load_source_files(config_path: str) -> Dict[str, Any]:
    """Read config/config.yaml and return the source_files section"""
    try:
        cfg = yaml.safe_load(open(config_path, "r", encoding="utf-8"))
        return cfg.get("source_files", {})
    except Exception:
        return {}


def extract_identifiers(app_repo: str, config_path: str, logical_name: str) -> Dict[str, str]:
    """
    Scan configured source files (React Native, iOS, Android) for identifiers.
    logical_name is a hint (like 'menuSettingsButton' or 'hoodieproduct').
    Returns {"android": "...", "ios": "...", "rn": "..."} if found.
    """
    source_files = load_source_files(config_path)
    android_id, ios_id, rn_id = None, None, None

    # React Native: look for testID="xxx"
    for relpath in source_files.get("react_native", []):
        path = os.path.join(app_repo, relpath)
        if not os.path.exists(path):
            continue
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
        m = re.search(rf'testID\s*=\s*"([^"]*{logical_name}[^"]*)"', text)
        if m:
            rn_id = m.group(1)
            break

    # iOS Native: look for accessibilityIdentifier or Swift property
    for relpath in source_files.get("ios_native", []):
        path = os.path.join(app_repo, relpath)
        if not os.path.exists(path):
            continue
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
        m = (
            re.search(rf'accessibilityIdentifier\s*=\s*"([^"]*{logical_name}[^"]*)"', text)
            or re.search(rf'{logical_name}\w*Button', text)
        )
        if m:
            ios_id = m.group(1) if m.lastindex else m.group(0)
            break

    # Android Native: look for resource-ids
    for relpath in source_files.get("android_native", []):
        path = os.path.join(app_repo, relpath)
        if not os.path.exists(path):
            continue
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
        m = re.search(rf'@id/({logical_name}\w*)', text) or re.search(
            rf'com\.walmart\.android\.debug:id/({logical_name}\w*)', text
        )
        if m:
            android_id = f"com.walmart.android.debug:id/{m.group(1)}"
            break

    return {"android": android_id, "ios": ios_id, "react_native": rn_id}


# --- Back-compat shim (needed by cli.py) ---
def rn_value_to_platform_locators(testid_value: str) -> dict:
    """
    Back-compat shim used by cli.py.
    Given a React Native testID value, produce cross-platform locators
    that line up with your YAML style.
    """
    android = f"//*[@content-desc='{testid_value}'] | //*[@resource-id='{testid_value}']"
    ios = f"//*[@name='{testid_value}']"
    return {"android": android, "ios": ios}


__all__ = [
    "extract_identifiers",
    "load_source_files",
    "rn_value_to_platform_locators",
]
