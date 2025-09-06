
def build_locator_fix_prompt(test_name: str, error_trace: str, old_key: str, snapshot_excerpt: str):
    return f"Fix locator for {test_name} failing with {error_trace}. Old key: {old_key}. Snippet: {snapshot_excerpt}"
