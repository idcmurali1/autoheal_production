# autoheal/retriever.py
from __future__ import annotations
import os, json, glob, hashlib
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Iterable, Optional, Tuple

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return None

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()


@dataclass
class Doc:
    id: str
    text: str
    meta: Dict[str, Any]

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "meta": self.meta}


class LocalVectorStore:
    """
    Very light-weight TF-IDF vector store.
    Persists documents and model artifacts under base_path.
    """
    def __init__(self, base_path: str):
        self.base_path = base_path
        _ensure_dir(self.base_path)
        self.docs_dir = os.path.join(self.base_path, "docs")
        _ensure_dir(self.docs_dir)
        self.index_file = os.path.join(self.base_path, "tfidf_index.json")
        self.vectorizer_file = os.path.join(self.base_path, "tfidf_vocab.json")

        self.vectorizer: Optional[TfidfVectorizer] = None
        self.matrix = None  # scipy sparse
        self.doc_ids: List[str] = []
        self.loaded = False

    # ---------- persistence ----------
    def _load_corpus(self) -> List[Doc]:
        items: List[Doc] = []
        for p in glob.glob(os.path.join(self.docs_dir, "*.json")):
            try:
                d = json.load(open(p, "r", encoding="utf-8"))
                items.append(Doc(id=d["id"], text=d["text"], meta=d.get("meta", {})))
            except Exception:
                continue
        return items

    def add(self, docs: Iterable[Doc]) -> None:
        for d in docs:
            json_path = os.path.join(self.docs_dir, f"{d.id}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(d.to_json(), f, ensure_ascii=False)
        # retrain index incrementally (simple: rebuild)
        self.rebuild()

    def rebuild(self) -> None:
        corpus = self._load_corpus()
        texts = [d.text for d in corpus]
        self.doc_ids = [d.id for d in corpus]
        if not texts:
            # empty index
            self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
            self.matrix = None
            self.loaded = True
            return

        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
        self.matrix = self.vectorizer.fit_transform(texts)
        # persist vocab (compact)
        with open(self.vectorizer_file, "w", encoding="utf-8") as f:
            json.dump(self.vectorizer.vocabulary_, f)
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump({"doc_ids": self.doc_ids}, f)
        self.loaded = True

    def load(self) -> None:
        # lazy load (rebuild from docs dir if vocab missing)
        if os.path.exists(self.vectorizer_file) and os.path.exists(self.index_file):
            try:
                vocab = json.load(open(self.vectorizer_file, "r", encoding="utf-8"))
                self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=1, vocabulary=vocab)
                meta = json.load(open(self.index_file, "r", encoding="utf-8"))
                self.doc_ids = meta.get("doc_ids", [])
                # build matrix from corpus using loaded vocab
                corpus = self._load_corpus()
                texts = [d.text for d in corpus]
                self.matrix = self.vectorizer.fit_transform(texts) if texts else None
                self.loaded = True
                return
            except Exception:
                pass
        # fallback: rebuild from scratch
        self.rebuild()

    def search(self, query: str, k: int = 5) -> List[Tuple[str, float]]:
        if not self.loaded:
            self.load()
        if not self.vectorizer or self.matrix is None or self.matrix.shape[0] == 0:
            return []
        q = self.vectorizer.transform([query])
        sims = cosine_similarity(q, self.matrix).ravel()
        idx = sims.argsort()[::-1][:k]
        return [(self.doc_ids[i], float(sims[i])) for i in idx if sims[i] > 0]


class LocalRetriever:
    """
    High-level API used by cli.py.
    Documents are stored as JSON (one per file) under vector_index/docs.
    """
    def __init__(self, base_path: str):
        self.store = LocalVectorStore(base_path)

    def topk(self, query: Dict[str, Any], k: int = 5) -> List[Dict[str, Any]]:
        # naive query text: concat all values
        parts = []
        for v in query.values():
            if isinstance(v, str):
                parts.append(v)
        q = " ".join(parts) if parts else json.dumps(query)
        hits = self.store.search(q, k=k)
        out: List[Dict[str, Any]] = []
        for doc_id, score in hits:
            p = os.path.join(self.store.docs_dir, f"{doc_id}.json")
            try:
                d = json.load(open(p, "r", encoding="utf-8"))
                d["score"] = score
                out.append(d)
            except Exception:
                continue
        return out
