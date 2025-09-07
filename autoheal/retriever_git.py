# autoheal/retriever_git.py
from __future__ import annotations
import os, json, subprocess, textwrap
from typing import List, Dict, Any
from .retriever import Doc, LocalVectorStore, _sha

def _git(app_repo: str, *args: str) -> str:
    res = subprocess.run(
        ["git"] + list(args),
        cwd=app_repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return res.stdout

def _recent_commits(app_repo: str, n: int) -> List[str]:
    out = _git(app_repo, "rev-list", "--max-count", str(n), "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]

class GitHistoryRetriever:
    """
    Builds a small local RAG index from recent git commits in the application repo.
    We store per-commit 'documents' containing messages and diffs.
    """
    def __init__(self, base_path: str):
        self.store = LocalVectorStore(base_path)

    def ingest_commits(self, app_repo: str, n_commits: int = 50) -> int:
        commits = _recent_commits(app_repo, n_commits)
        docs: List[Doc] = []
        for sha in commits:
            msg = _git(app_repo, "log", "-1", "--pretty=%B", sha).strip()
            diff = _git(app_repo, "show", "--pretty=", "--unified=0", sha)
            text = textwrap.dedent(f"""
                COMMIT: {sha}
                MESSAGE:
                {msg}

                DIFF (u=0):
                {diff}
            """).strip()
            doc_id = _sha(sha + msg[:200])
            docs.append(Doc(id=doc_id, text=text, meta={"commit": sha, "message": msg}))
        if docs:
            self.store.add(docs)
        return len(docs)

    def topk(self, query: Dict[str, Any], k: int = 5) -> List[Dict[str, Any]]:
        # delegate to underlying store
        from .retriever import LocalRetriever
        return LocalRetriever(self.store.base_path).topk(query, k=k)
