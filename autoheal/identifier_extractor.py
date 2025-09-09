from __future__ import annotations
import os, re, yaml
from typing import Dict, Any, List, Tuple, Optional

# ---------------- IO helpers ----------------
def _read(path: str) -> str:
    try:
        return open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""

def _walk(root: str, exts: Tuple[str, ...]) -> List[str]:
    out: List[str] = []
    for dp, _, files in os.walk(root):
        for fn in files:
            if fn.endswith(exts):
                out.append(os.path.join(dp, fn))
    return out

# ---------------- config helpers ----------------
def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        return yaml.safe_load(open(path, "r", encoding="utf-8"))
    except Exception:
        return {}

def load_source_files(config_path: str) -> Dict[str, Any]:
    cfg = _load_yaml(config_path)
    return cfg.get("source_files", {}) or {}

def load_app_mapping(config_path: str) -> Dict[str, Any]:
    cfg = _load_yaml(config_path)
    return cfg.get("app", {}) or {}

# ---------------- RN locators shim (for YAML style) ----------------
def rn_value_to_platform_locators(testid_value: str) -> dict:
    """
    Given a React Native testID value, produce cross-platform locators
    compatible with your YAML mappings.
    """
    android = f"//*[@content-desc='{testid_value}'] | //*[@resource-id='{testid_value}']"
    ios = f"//*[@name='{testid_value}']"
    return {"android": android, "ios": ios}

# ---------------- pattern & fuzzy matching ----------------
def map_by_patterns(value: str, patterns: List[Dict[str, str]]) -> Optional[str]:
    """
    patterns: [{match: '^regex$', logical: 'us.mappings....'}, ...]
    returns logical name or None
    """
    for p in patterns or []:
        try:
            if re.search(p.get("match", ""), value):
                return p.get("logical")
        except re.error:
            continue
    return None

def normalize_base(value: str) -> str:
    """
    Collapse common suffix variations so related IDs map to the same logical.
    Examples:
      product_sku_hoodie           -> product_sku_hoodie
      product_sku_hoodiePremium    -> product_sku_hoodie
      product_sku_hoodie_val1      -> product_sku_hoodie
      product_sku_hoodie123        -> product_sku_hoodie
    """
    v = value.strip()
    v = re.sub(r"[_-]val\d+$", "", v, flags=re.IGNORECASE)   # _valNN
    v = re.sub(r"\d+$", "", v)                               # trailing digits
    v = re.sub(r"(Premium|Plus|Deluxe|V\d+)$", "", v, flags=re.IGNORECASE)
    return v

def map_by_fuzzy(value: str, exact_map: Dict[str, str]) -> Optional[str]:
    base = normalize_base(value)
    if base in exact_map:
        return exact_map[base]
    for k, logical in exact_map.items():
        if value.startswith(k) or base.startswith(k):
            return logical
    return None

# ---------------- Extractors ----------------
RN_TESTID_RE = re.compile(r"\btestID\s*[:=]\s*['\"]([^'\"\n]+)['\"]")

def extract_rn_testids(app_repo: str, config_path: str) -> List[str]:
    files_cfg = load_source_files(config_path).get("react_native", [])
    files = [os.path.join(app_repo, p) for p in files_cfg if os.path.exists(os.path.join(app_repo, p))]
    if not files:
        files = _walk(app_repo, (".ts", ".tsx", ".js", ".jsx"))

    seen: Dict[str, None] = {}
    for p in files:
        for m in RN_TESTID_RE.finditer(_read(p)):
            seen.setdefault(m.group(1).strip(), None)
    return list(seen.keys())

def extract_ios_identifiers(app_repo: str, config_path: str) -> List[str]:
    files_cfg = load_source_files(config_path).get("ios_native", [])
    files = [os.path.join(app_repo, p) for p in files_cfg if os.path.exists(os.path.join(app_repo, p))]
    if not files:
        files = _walk(app_repo, (".swift", ".m", ".mm"))

    ios_ids: Dict[str, None] = {}
    pat = re.compile(r'accessibilityIdentifier\s*=\s*["\']([^"\']+)["\']')
    for p in files:
        txt = _read(p)
        for m in pat.finditer(txt):
            ios_ids.setdefault(m.group(1), None)
        # fallback: common control names
        for m in re.finditer(r'\b([A-Za-z0-9_]*SettingsButton[A-Za-z0-9_]*)\b', txt):
            ios_ids.setdefault(m.group(1), None)
    return list(ios_ids.keys())

def extract_android_identifiers(app_repo: str, config_path: str) -> List[str]:
    files_cfg = load_source_files(config_path).get("android_native", [])
    files = [os.path.join(app_repo, p) for p in files_cfg if os.path.exists(os.path.join(app_repo, p))]
    if not files:
        files = _walk(app_repo, (".xml", ".kt", ".java"))

    out: Dict[str, None] = {}
    pat_xml = re.compile(r'@[\+]?id/([A-Za-z0-9_\.]+)')  # XML resource-ids
    pat_cd  = re.compile(r'contentDescription\s*=\s*["\']([^"\']+)["\']')
    pat_fq  = re.compile(r'com\.walmart\.android\.debug:id/([A-Za-z0-9_\.]+)')

    for p in files:
        txt = _read(p)
        for m in pat_xml.finditer(txt):
            out.setdefault(m.group(1), None)
        for m in pat_cd.finditer(txt):
            out.setdefault(m.group(1), None)
        for m in pat_fq.finditer(txt):
            out.setdefault(f"com.walmart.android.debug:id/{m.group(1)}", None)
    return list(out.keys())

# ---------------- Public API ----------------
def extract_identifiers(app_repo: str, platform: str, config_path: str) -> Dict[str, List[str]]:
    """
    Returns dict with a single platform key -> list of discovered identifiers.
      react_native: [testIDs...]
      ios_native:   [accessibilityIdentifier or names...]
      android_native:[resource-ids or content-desc...]
    """
    if platform == "react_native":
        return {"react_native": extract_rn_testids(app_repo, config_path)}
    if platform == "ios_native":
        return {"ios_native": extract_ios_identifiers(app_repo, config_path)}
    if platform == "android_native":
        return {"android_native": extract_android_identifiers(app_repo, config_path)}
    return {}

def choose_logical_for_rn(testid: str, exact_map: Dict[str, str], patterns: List[Dict[str, str]]) -> Optional[str]:
    """
    Decide which logical name a given RN testID belongs to:
      1) exact map (highest priority)
      2) regex pattern list
      3) fuzzy base-name mapping
    """
    if testid in exact_map:
        return exact_map[testid]
    m = map_by_patterns(testid, patterns)
    if m:
        return m
    return map_by_fuzzy(testid, exact_map)

__all__ = [
    "extract_identifiers",
    "load_source_files",
    "rn_value_to_platform_locators",
    "choose_logical_for_rn",
]
