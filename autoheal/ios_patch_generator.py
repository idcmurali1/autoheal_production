
from .ios_xml import parse_xml, find_button_with_label, find_button_label_candidates

def generate_ios_locator_patch(old_predicate_or_label: str, xml_text: str, prefer_accessibility=True):
    root = parse_xml(xml_text)
    if root is None:
        return None

    target_label = old_predicate_or_label
    if "label" in old_predicate_or_label and '"' in old_predicate_or_label:
        try:
            target_label = old_predicate_or_label.split('label')[1].split('"')[1]
        except Exception:
            pass

    candidates = find_button_label_candidates(root)
    likely_new = None
    for lbl in candidates:
        if target_label.lower() in lbl.lower() and lbl != target_label:
            likely_new = lbl
            break
    if likely_new is None and candidates:
        likely_new = candidates[0]

    chosen_node = find_button_with_label(root, likely_new) if likely_new else None
    if prefer_accessibility and chosen_node is not None:
        acc_id = chosen_node.attrib.get("name") or chosen_node.attrib.get("accessibilityIdentifier")
        if acc_id:
            return {"strategy":"accessibility_id","value":acc_id,
                    "rationale": f'Preferred accessibility id "{acc_id}" on button labeled "{likely_new}".'}

    if likely_new:
        return {"strategy":"ios_predicate","value": f'label == "{likely_new}"',
                "rationale": f'Updated label from "{target_label}" to "{likely_new}".'}
    return None
