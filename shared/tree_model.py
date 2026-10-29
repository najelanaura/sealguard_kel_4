"""Decision Tree hasil train_tree.py, dibaca dari model.json.

Kenapa JSON dan bukan pickle/sklearn di runtime? Detector cukup menelusuri pohon
(if/else), jadi tidak perlu scikit-learn terpasang di mesin yang menjalankannya,
dan isi pohonnya bisa dibaca manusia untuk dokumentasi.
"""
import json


class TreeModel:
    def __init__(self, spec: dict):
        self.features = spec["features"]
        self.nodes = spec["nodes"]
        self.classes = spec.get("classes", [])
        self.meta = spec.get("meta", {})

    @classmethod
    def load(cls, path: str) -> "TreeModel":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def predict(self, p: dict) -> str:
        x = {name: float(p.get(name) or 0.0) for name in self.features}  # bool -> 0/1
        i = 0
        while not self.nodes[i]["leaf"]:
            n = self.nodes[i]
            i = n["left"] if x[n["feature"]] <= n["threshold"] else n["right"]
        return self.nodes[i]["label"]
