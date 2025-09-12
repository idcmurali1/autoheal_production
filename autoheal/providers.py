# autoheal/providers.py
from typing import Any, Dict
import re

class OpenAIChatLLM:
    """
    Minimal, local 'OpenAI-like' LLM. It returns concrete suggestions for
    unmapped identifiers so the pipeline can progress end-to-end.
    """
    def __init__(self, api_key: str, model: str = "gpt-4o", temperature: float = 0.1):
        self.api_key = api_key or ""
        self.model = model or "gpt-4o"
        self.temperature = float(temperature or 0.0)

    def generate_patch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if isinstance(payload, dict) and payload.get("task") == "map_identifiers":
                platform = payload.get("platform", "react_native")
                identifiers = payload.get("identifiers", []) or []
                suggestions = []

                for ident in identifiers:
                    # Heuristic logical name guesses
                    if ident.startswith("product_sku_"):
                        name = ident.split("product_sku_", 1)[-1]
                        known = {
                            "hoodie": "us.mappings.yourOrders.hoodieProduct",
                            "cap": "us.mappings.yourOrders.capProduct",
                            "shirt": "us.mappings.yourOrders.shirtProduct",
                            "bottle": "us.mappings.yourOrders.bottleProduct",
                            "headphones": "us.mappings.yourOrders.headphonesProduct",
                            "vip": "us.mappings.yourOrders.vipProduct",
                        }
                        logical = known.get(name, f"us.mappings.catalog.{name}Product")
                    else:
                        safe = re.sub(r"[^a-zA-Z0-9]+", "_", ident).strip("_")
                        logical = f"us.mappings.auto.{safe}"

                    # Platform locators
                    if platform == "react_native":
                        android = f"//*[@content-desc='{ident}'] | //*[@resource-id='{ident}']"
                        ios = f"//*[@name='{ident}']"
                    elif platform == "ios_native":
                        android, ios = "", f"//*[@name='{ident}']"
                    else:  # android_native
                        android, ios = f"//*[@content-desc='{ident}'] | //*[@resource-id='{ident}']", ""

                    suggestions.append({
                        "identifier": ident,
                        "logical": logical,
                        "android": android,
                        "ios": ios,
                    })

                return {"suggestions": suggestions, "model": self.model}

            # Non-mapping calls: benign noop
            return {
                "patch": "noop",
                "explanation": "rulebased fallback",
                "prompt_summary": str(payload)[:2000],
            }
        except Exception as e:
            return {"patch": "noop", "error": str(e), "prompt_summary": str(payload)[:2000]}


class _RuleBasedLLM:
    def __init__(self, *_, **__): pass
    def generate_patch(self, prompt: Dict[str, Any]) -> Dict[str, Any]:
        return {"patch": "noop", "explanation": "rulebased fallback", "prompt_summary": str(prompt)[:500]}


def get_llm(provider: str, openai_key: str, anthropic_key: str, model: str, temperature: float):
    """
    Provider multiplexer.
    - 'openai' -> OpenAIChatLLM (local heuristic version here; swap to real API later)
    - 'rulebased' (or anything else without a key) -> _RuleBasedLLM
    """
    p = (provider or "").strip().lower()
    if p in ("openai", "open_ai", "oai"):
        return OpenAIChatLLM(openai_key, model or "gpt-4o", temperature or 0.1)
    if p in ("rulebased", "rule-based", "stub", "fake"):
        return _RuleBasedLLM()
    # Fallback: prefer OpenAI-style if key present, else rulebased
    return OpenAIChatLLM(openai_key, model or "gpt-4o", temperature or 0.1) if openai_key else _RuleBasedLLM()
