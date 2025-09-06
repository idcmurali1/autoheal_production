
import traceback, importlib

def run_test_and_capture(test_module_path: str):
    try:
        mod = importlib.import_module(test_module_path)
        importlib.reload(mod)
        passed = bool(mod.run_test())
        if passed:
            return True, None
        else:
            return False, "AssertionError: locator not found"
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"
