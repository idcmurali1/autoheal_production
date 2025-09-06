from typing import List, Dict, Any
class GitHistoryRetriever:
    def __init__(self, base_path: str):
        self.base_path = base_path
    def ingest_commits(self, repo_path: str, limit: int = 50):
        # no-op stub
        return
    def topk(self, query: Dict[str, Any], k: int = 10) -> List[Dict[str, Any]]:
        return []
