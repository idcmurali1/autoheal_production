import os

def find_and_replace_text(root: str, old: str, new: str):
    changed = 0
    scanned = 0
    details = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
            except Exception:
                continue
            scanned += 1
            if old in data:
                data2 = data.replace(old, new)
                if data2 != data:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(data2)
                    changed += 1
                    details.append(path)
    return {"scanned": scanned, "changed_files": changed, "files": details}
