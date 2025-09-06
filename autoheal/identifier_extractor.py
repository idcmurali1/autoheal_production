import os, re

def extract_menu_settings_identifiers(app_repo: str):
    # naive scan for plausible identifiers
    android_id = None
    ios_id = None

    # Android: look for account_header_settings id
    for dirpath, _, files in os.walk(app_repo):
        for fn in files:
            if fn.endswith((".xml", ".kt", ".java", ".tsx", ".jsx")):
                path = os.path.join(dirpath, fn)
                try:
                    text = open(path, "r", encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                m = re.search(r'@\+id/(account_header_settings\w*)', text) or                     re.search(r'com\.walmart\.android\.debug:id/(account_header_settings\w*)', text)
                if m:
                    android_id = f"com.walmart.android.debug:id/{m.group(1)}"
                    break
        if android_id: break

    # iOS: simple search for settingsButton identifier
    for dirpath, _, files in os.walk(app_repo):
        for fn in files:
            if fn.endswith((".swift", ".m", ".mm", ".tsx", ".jsx")):
                path = os.path.join(dirpath, fn)
                try:
                    text = open(path, "r", encoding="utf-8", errors="ignore").read()
                except Exception:
                    continue
                m = re.search(r'HeaderV\d+View\.settingsButton', text) or                     re.search(r'accessibilityIdentifier\s*=\s*"([A-Za-z0-9_\.]+)"', text)
                if m:
                    ios_id = m.group(0) if "." in m.group(0) else m.group(1)
                    break
        if ios_id: break

    return {"android": android_id, "ios": ios_id}
