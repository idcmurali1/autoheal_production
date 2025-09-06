
from .retriever import SimpleRetriever
def generate_locator_patch(old_id: str, html_text: str):
    retriever = SimpleRetriever(html_text)
    new_id = retriever.guess_replacement_for(old_id)
    if not new_id:
        return None
    return {"new_id": new_id, "rationale": f"Replaced '{old_id}' with '{new_id}' via HTML heuristic."}
