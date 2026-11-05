#!/usr/bin/env python3
"""Latih Decision Tree (Supervised Learning) dari data/drops.csv.

  A (input)  : impact_peak_g, impact_count, rotation_rad, freefall, lux_avg
  B (output) : TERSEGEL | BENTURAN | TERBUKA | RUSAK

  pip install -r requirements-train.txt
  python train_tree.py

Keluaran:
  models/model.json          model untuk detector.py (dilatih ulang dari SEMUA data)
  reports/ai_report.md       laporan siap tempel ke PPT: data, akurasi uji, confusion matrix, pohon
  reports/metrics.json       angka mentah
  reports/confusion_matrix.png   (kalau matplotlib terpasang)

Cara evaluasi: data dibagi latih/uji PER SESI collect.py (bukan per baris). Window dari satu
sesi saling mirip; kalau dicampur ke latih dan uji, akurasi tampak bagus padahal bocor.
"""
import argparse
import csv
import datetime
import json
import os
import random
import sys
from collections import Counter, defaultdict

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier, export_text

from shared.config import Thresholds
from shared.features import CLASSES, FEATURES
from shared.rules import PacketJudge


def _f(x, default=0.0):
    try:
        return float(x) if str(x).strip() != "" else default
    except ValueError:
        return default


def load_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            if r.get("label") not in CLASSES:
                continue
            lux_raw = r.get("lux_avg", "")
            rows.append({
                "label": r["label"],
                "session": r.get("session") or f"baris{i}",
                "has_session": bool(r.get("session")),
                "impact_peak_g": _f(r["impact_peak_g"]),
                "impact_count": _f(r.get("impact_count")),
                "rotation_rad": _f(r.get("rotation_rad")),
                "freefall": _f(r.get("freefall")),
                "lux_avg": _f(lux_raw) if str(lux_raw).strip() != "" else None,
            })
    return rows


def vec(r):
    return [(0.0 if r[k] is None else float(r[k])) for k in FEATURES]


def split_data(rows, test_frac, seed):
    """Return (train, test, mode). Prefer per sesi; fallback per baris; atau tanpa uji."""
    sessions = defaultdict(set)
    for r in rows:
        sessions[r["label"]].add(r["session"])
    if all(r["has_session"] for r in rows) and all(len(s) >= 2 for s in sessions.values()):
        rng, test_s = random.Random(seed), set()
        for lab in sorted(sessions):
            s = sorted(sessions[lab])
            rng.shuffle(s)
            k = min(max(1, round(len(s) * test_frac)), len(s) - 1)
            test_s.update(s[:k])
        train = [r for r in rows if r["session"] not in test_s]
        test = [r for r in rows if r["session"] in test_s]
        return train, test, "per sesi (disarankan)"
    counts = Counter(r["label"] for r in rows)
    if min(counts.values()) >= 2 and len(rows) >= 10:
        y = [r["label"] for r in rows]
        tr, te = train_test_split(rows, test_size=test_frac, random_state=seed, stratify=y)
        return tr, te, "per baris (berisiko bocor: tiap kelas < 2 sesi)"
    return rows, [], "tanpa data uji (data terlalu sedikit)"


def export_tree(clf, features):
    t = clf.tree_
    nodes = []
    for i in range(t.node_count):
        if t.children_left[i] == -1:
            nodes.append({"leaf": True, "label": str(clf.classes_[int(np.argmax(t.value[i][0]))])})
        else:
            nodes.append({"leaf": False, "feature": features[t.feature[i]],
                          "threshold": float(t.threshold[i]),
                          "left": int(t.children_left[i]), "right": int(t.children_right[i])})
    return nodes


def baseline_predict(rows, th):
    """Threshold buatan tangan (tanpa model) pada baris yang sama, sebagai pembanding."""
    out = []
    for r in rows:
        p = {"impact_peak_g": r["impact_peak_g"], "impact_count": r["impact_count"],
             "freefall": bool(r["freefall"]), "lux_avg": r["lux_avg"]}
        out.append(PacketJudge(th).classify(p))
    return out


def md_table(header, body):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in body]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/drops.csv")
    ap.add_argument("--out", default="models/model.json")
    ap.add_argument("--report-dir", default="reports")
    ap.add_argument("--depth", type=int, default=3, help="kedalaman maksimum (kecil = tidak overfit)")
    ap.add_argument("--min-leaf", type=int, default=2)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=42)
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
        print("PERINGATAN: ada kelas dengan < 10 baris. Hasil tidak bisa dipercaya; targetkan 15-30 per kelas.")

    th = Thresholds.from_env()
    train, test, mode = split_data(rows, args.test_frac, args.seed)
    labels = [c for c in CLASSES if c in counts]

    def new_clf():
        return DecisionTreeClassifier(max_depth=args.depth, min_samples_leaf=args.min_leaf,
                                      class_weight="balanced", random_state=0)

    metrics = {"split_mode": mode, "n_train": len(train), "n_test": len(test),
               "class_counts": dict(counts), "test_accuracy": None, "baseline_accuracy": None}
    report = None
    cm = None
    if test:
        clf_eval = new_clf().fit(np.array([vec(r) for r in train]), [r["label"] for r in train])
        y_true = [r["label"] for r in test]
        y_pred = list(clf_eval.predict(np.array([vec(r) for r in test])))
        y_base = baseline_predict(test, th)
        metrics["test_accuracy"] = round(accuracy_score(y_true, y_pred), 4)
        metrics["baseline_accuracy"] = round(accuracy_score(y_true, y_base), 4)
        cm = confusion_matrix(y_true, y_pred, labels=labels)
        report = classification_report(y_true, y_pred, labels=labels, zero_division=0, output_dict=True)
        print(f"\nPembagian data: {mode}; latih={len(train)} baris, uji={len(test)} baris")
        print(f"Akurasi uji Decision Tree : {metrics['test_accuracy']:.1%}")
        print(f"Akurasi uji threshold     : {metrics['baseline_accuracy']:.1%} (pembanding)")
        print(f"Confusion matrix (baris = sebenarnya, kolom = prediksi; urutan {labels}):")
        print(cm)
    else:
        print("\nPERINGATAN: tidak ada data uji, akurasi tidak bisa dilaporkan.")

    # model akhir: dilatih dari SEMUA data supaya memakai contoh sebanyak mungkin
    clf = new_clf().fit(np.array([vec(r) for r in rows]), [r["label"] for r in rows])
    tree_text = export_text(clf, feature_names=FEATURES)
    importance = {f: round(float(v), 3) for f, v in zip(FEATURES, clf.feature_importances_)}
    print("\nAturan pohon:\n" + tree_text)
    print("Pentingnya fitur:", importance)

    spec = {
        "features": FEATURES,
        "classes": [str(c) for c in clf.classes_],
        "nodes": export_tree(clf, FEATURES),
        "meta": {"trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
                 "n_samples": len(rows), "class_counts": dict(counts), "max_depth": args.depth,
                 "split_mode": mode, "test_accuracy": metrics["test_accuracy"],
                 "baseline_accuracy": metrics["baseline_accuracy"]},
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    os.makedirs(args.report_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)

    sess = defaultdict(set)
    for r in rows:
        sess[r["label"]].add(r["session"])
    metrics.update({"feature_importance": importance,
                    "confusion_matrix": None if cm is None else {"labels": labels, "matrix": cm.tolist()},
                    "per_class": None if report is None else {k: report[k] for k in labels}})
    with open(os.path.join(args.report_dir, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    md = ["# Laporan AI: Supervised Learning (Decision Tree)", "",
          f"Dibuat otomatis oleh `train_tree.py` pada {spec['meta']['trained_at']}.", "",
          "**Input A**: " + ", ".join(FEATURES), "",
          "**Output B**: " + ", ".join(CLASSES), "", "## Data", "",
          md_table(["Label", "Baris", "Sesi"], [[c, counts.get(c, 0), len(sess.get(c, []))] for c in CLASSES]), "",
          f"Pembagian: **{mode}**; latih {len(train)} baris, uji {len(test)} baris.", ""]
    if test:
        md += ["## Hasil pada data uji", "",
               md_table(["Metode", "Akurasi"], [["Decision Tree", f"{metrics['test_accuracy']:.1%}"],
                                                ["Threshold (pembanding)", f"{metrics['baseline_accuracy']:.1%}"]]), "",
               md_table(["Kelas", "Precision", "Recall", "F1", "Jumlah uji"],
                        [[k, f"{report[k]['precision']:.2f}", f"{report[k]['recall']:.2f}",
                          f"{report[k]['f1-score']:.2f}", int(report[k]['support'])] for k in labels]), "",
               "### Confusion matrix (baris = sebenarnya, kolom = prediksi)", "",
               md_table([""] + labels, [[labels[i]] + [int(v) for v in cm[i]] for i in range(len(labels))]), ""]
    md += ["## Aturan pohon", "", "```", tree_text.strip(), "```", "",
           "## Pentingnya fitur", "", md_table(["Fitur", "Skor"], [[k, v] for k, v in importance.items()]), "",
           "## Catatan", "",
           "- Label berasal dari protokol/kriteria kelompok, bukan dari ambang di kode.",
           "- Data sedikit: akurasi uji punya ketidakpastian besar, laporkan jumlah baris dan sesi apa adanya.",
           "- Model akhir dilatih dari semua data (latih + uji); angka akurasi di atas dari model yang dilatih hanya pada data latih.", ""]
    with open(os.path.join(args.report_dir, "ai_report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))

    if cm is not None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(4.2, 3.8))
            ax.imshow(cm, cmap="Blues")
            ax.set_xticks(range(len(labels)), labels, rotation=30, ha="right")
            ax.set_yticks(range(len(labels)), labels)
            for i in range(len(labels)):
                for j in range(len(labels)):
                    ax.text(j, i, int(cm[i][j]), ha="center", va="center")
            ax.set_xlabel("Prediksi"); ax.set_ylabel("Sebenarnya"); ax.set_title("Confusion matrix (data uji)")
            fig.tight_layout()
            fig.savefig(os.path.join(args.report_dir, "confusion_matrix.png"), dpi=160)
        except ImportError:
            pass

    print(f"\nModel: {args.out} | Laporan: {args.report_dir}/ai_report.md")
    return metrics


if __name__ == "__main__":
    main()
