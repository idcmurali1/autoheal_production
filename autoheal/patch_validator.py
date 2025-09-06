
import difflib, importlib, os

def make_diff(original_text: str, patched_text: str, filename: str):
    return ''.join(difflib.unified_diff(
        original_text.splitlines(keepends=True),
        patched_text.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    ))

def _format_locator(strategy, value):
    if strategy == "accessibility_id":
        return "{'strategy': 'accessibility_id', 'value': " + repr(value) + "}"
    if strategy == "ios_predicate":
        return "{'strategy': 'ios_predicate', 'value': " + repr(value) + "}"
    return repr(value)

def apply_and_run(test_file: str, module_name: str, new_value):
    with open(test_file, "r") as f:
        orig = f.read()
    patched_lines = []
    for line in orig.splitlines():
        if line.strip().startswith("LOCATOR ="):
            if isinstance(new_value, dict):
                strategy = new_value.get('strategy')
                value = new_value.get('value')
                patched_lines.append(f"LOCATOR = {_format_locator(strategy, value)}")
            else:
                patched_lines.append(f"LOCATOR = {repr(new_value)}")
        else:
            patched_lines.append(line)
    patched_text = "\n".join(patched_lines) + "\n"
    diff = make_diff(orig, patched_text, os.path.basename(test_file))
    with open(test_file, "w") as f:
        f.write(patched_text)
    mod = importlib.import_module(module_name)
    importlib.reload(mod)
    passed = bool(mod.run_test())
    return passed, diff, patched_text, orig
