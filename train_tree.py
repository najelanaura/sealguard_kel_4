#!/usr/bin/env python3
"""Latih Decision Tree dari data/drops.csv -> model.json (dibaca detector.py).

  pip install -r requirements-train.txt
  python train_tree.py                 # hasil: model.json + laporan di layar

Laporan penting untuk dokumentasi: akurasi cross-validation, confusion matrix,
aturan pohon, dan perbandingan dengan threshold buatan tangan.
"""
import argparse
import csv
import datetime
import json
import sys
from collections import Counter

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.tree import DecisionTreeClassifier, export_text

from shared.config import Thresholds
from shared.rules import PacketJudge

FEATURES = ["impact_peak_g", "impact_count", "rotation_rad", "freefall"]
CLASSES = ["TERSEGEL", "BENTURAN", "RUSAK"]


def load_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["label"] not in CLASSES:
                continue
            rows.append({
                "label": r["label"],
                "impact_peak_g": float(r["impact_peak_g"]),
                "impact_count": float(r["impact_count"] or 0),
                "rotation_rad": float(r["rotation_rad"] or 0),
                "freefall": float(r["freefall"] or 0),
            })
    return rows


def export_tree(clf, features):
    t = clf.tree_
    nodes = []
    for i in range(t.node_count):
        if t.children_left[i] == -1:
            nodes.append({"leaf": True, "label": str(clf.classes_[int(np.argmax(t.value[i][0]))])})
        else:
            nodes.append({
                "leaf": False,
                "feature": features[t.feature[i]],
                "threshold": float(t.threshold[i]),
                "left": int(t.children_left[i]),
                "right": int(t.children_right[i]),
            })
    return nodes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/drops.csv")
    ap.add_argument("--out", default="model.json")
    ap.add_argument("--depth", type=int, default=3, help="kedalaman maksimum (kecil = tidak overfit)")
    ap.add_argument("--min-leaf", type=int, default=2)
    args = ap.parse_args(argv)

    rows = load_rows(args.data)
    counts = Counter(r["label"] for r in rows)
    print(f"Data: {len(rows)} baris -> {dict(counts)}")
    if len(counts) < 2:
        sys.exit("Butuh minimal 2 kelas berbeda. Kumpulkan data lagi dengan collect.py.")
    missing = [c for c in CLASSES if c not in counts]
    if missing:
        print(f"PERINGATAN: tidak ada data untuk {missing}. Model TIDAK akan pernah memprediksi kelas itu.")
    if min(counts.values()) < 10:
        print("PERINGATAN: ada kelas dengan < 10 sampel. Akurasi tidak bisa dipercaya; targetkan 15-30 per kelas.")

    X = np.array([[r[f] for f in FEATURES] for r in rows])
    y = np.array([r["label"] for r in rows])

    clf = DecisionTreeClassifier(max_depth=args.depth, min_samples_leaf=args.min_leaf,
                                 class_weight="balanced", random_state=0)

    labels = [c for c in CLASSES if c in counts]
    k = min(5, min(counts.values()))
    cv_acc = None
    if k >= 2:
        pred = cross_val_predict(clf, X, y, cv=StratifiedKFold(k, shuffle=True, random_state=0))
        cv_acc = accuracy_score(y, pred)
        print(f"\nAkurasi cross-validation ({k}-fold): {cv_acc:.1%}")
        print(f"Confusion matrix (baris = sebenarnya, kolom = prediksi; urutan {labels}):")
        print(confusion_matrix(y, pred, labels=labels))
    else:
        print("\nCross-validation dilewati (data terlalu sedikit).")

    # Pembanding: aturan threshold buatan tangan pada data yang sama
    judge = PacketJudge(Thresholds.from_env())
    base = [judge.impact_level({**r, "freefall": bool(r["freefall"])}) for r in rows]
    print(f"Pembanding threshold buatan tangan: {accuracy_score(y, base):.1%}")

    clf.fit(X, y)
    print("\nAturan pohon:")
    print(export_text(clf, feature_names=FEATURES))
    print("Pentingnya fitur:", {f: round(float(v), 3) for f, v in zip(FEATURES, clf.feature_importances_)})

    spec = {
        "features": FEATURES,
        "classes": [str(c) for c in clf.classes_],
        "nodes": export_tree(clf, FEATURES),
        "meta": {
            "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "n_samples": len(rows),
            "class_counts": dict(counts),
            "cv_accuracy": round(cv_acc, 3) if cv_acc is not None else None,
            "max_depth": args.depth,
        },
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)
    print(f"\nModel disimpan ke {args.out}. Jalankan ulang detector.py untuk memakainya.")


if __name__ == "__main__":
    main()
