
from xml.etree import ElementTree as ET

def parse_xml(xml_text: str):
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None

def iter_nodes(root):
    if root is None:
        return
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(list(n))

def get_attr(n, key):
    return n.attrib.get(key) or ""

def find_by_accessibility_id(root, acc_id: str):
    for n in iter_nodes(root):
        name = get_attr(n, "name")
        if name == acc_id:
            return n
        aid = get_attr(n, "accessibilityIdentifier")
        if aid == acc_id:
            return n
    return None

def find_button_with_label(root, label_exact: str):
    for n in iter_nodes(root):
        t = get_attr(n, "type")
        lbl = get_attr(n, "label")
        if t.endswith("Button") and lbl == label_exact:
            return n
    return None

def find_button_label_candidates(root):
    out = []
    for n in iter_nodes(root):
        t = get_attr(n, "type")
        lbl = get_attr(n, "label")
        if t.endswith("Button") and lbl:
            out.append(lbl)
    seen=set(); uniq=[]
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return uniq
