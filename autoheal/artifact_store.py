
from dataclasses import dataclass
import json, os, time

@dataclass
class Artifact:
    name: str
    path: str
    meta: dict

class ArtifactStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def save_json(self, name: str, obj: dict) -> Artifact:
        path = os.path.join(self.base_dir, f"{name}.json")
        with open(path, "w") as f:
            json.dump(obj, f, indent=2)
        return Artifact(name=name, path=path, meta={"type": "json", "ts": time.time()})

    def save_text(self, name: str, text: str) -> Artifact:
        path = os.path.join(self.base_dir, f"{name}.txt")
        with open(path, "w") as f:
            f.write(text)
        return Artifact(name=name, path=path, meta={"type": "text", "ts": time.time()})
