from typing import Any, Dict
class _RuleBasedLLM:
    def __init__(self, *_, **__): pass
    def generate_patch(self, prompt: Dict[str, Any]) -> Dict[str, Any]:
        # Deterministic placeholder; your real LLM can replace this
        return {"patch": "noop", "explanation": "rulebased fallback", "prompt_summary": str(prompt)[:500]}

def get_llm(provider: str, openai_key: str, anthropic_key: str, model: str, temperature: float):
    # Only rulebased is shipped to avoid secrets; plug real providers later
    return _RuleBasedLLM()
