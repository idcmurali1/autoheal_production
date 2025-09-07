# autoheal/prompt_builder.py
from typing import Any, Dict, List

def _shrink(text: str, limit: int = 8000) -> str:
    if not isinstance(text, str):
        return ""
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"

class PromptBuilder:
    """
    Backward-compatible prompt builder.
    cli.py expects: PromptBuilder().build(failure: dict, retrieved: list) -> dict
    """

    def __init__(self, system_hint: str = (
        "You are a senior test engineering assistant. "
        "Given failing test context and repo history snippets, propose the smallest, "
        "safest patch that fixes selectors/identifiers or text expectations. "
        "Return a unified diff when a code change is required."
    )):
        self.system_hint = system_hint

    def build(self, failure: Dict[str, Any], retrieved: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Normalize inputs into a single JSON prompt payload the LLM can consume.
        - failure keys we look for (all optional): test_name, broken_locator,
          logs, dom_snapshot_path, workspace, expected_text
        - retrieved: list of dicts (commit metadata, prior diffs, snippets, etc.)
        """
        # Normalize retrieved context: keep only helpful keys and cap length.
        context: List[Dict[str, Any]] = []
        for r in (retrieved or [])[:10]:
            if isinstance(r, dict):
                context.append({
                    k: r.get(k) for k in (
                        "path", "file", "commit", "sha", "author", "date",
                        "message", "diff", "snippet", "text", "reason"
                    ) if k in r
                })

        prompt: Dict[str, Any] = {
            "system": self.system_hint,
            "task": "propose_patch",
            "failure": {
                "test_name": failure.get("test_name", "unknown"),
                "broken_locator": failure.get("broken_locator"),
                "expected_text": failure.get("expected_text"),
                "dom_snapshot_path": failure.get("dom_snapshot_path", ""),
                "workspace": failure.get("workspace", ""),
                "logs": _shrink(failure.get("logs", "")),
            },
            "context": context,
            "constraints": {
                "patch_format": "unified_diff",
                "prefer_minimal_change": True,
                "preserve_style": True,
            },
        }
        return prompt


# Keep your existing helper (still exported) for direct, simple prompts.
def build_locator_fix_prompt(test_name: str, error_trace: str, old_key: str, snapshot_excerpt: str):
    return (
        f"Fix locator for {test_name} failing with {error_trace}. "
        f"Old key: {old_key}. Snippet: {snapshot_excerpt}"
    )

__all__ = ["PromptBuilder", "build_locator_fix_prompt"]
