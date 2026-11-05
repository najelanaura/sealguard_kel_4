#!/usr/bin/env python3
"""Kumpulkan data latih: catat telemetry edge1 ke CSV beserta label dari kamu.

Alur per sesi percobaan (detector.py TIDAK perlu jalan):
  1. python edge.py --device-id edge1                 # di HP dalam kardus
  2. python collect.py --label RUSAK --height-cm 50 --padding tidak --note "50cm tanpa bantalan"
  3. lakukan skenario 10-15 kali, Ctrl+C
  4. ganti --label / --note, ulangi untuk skenario berikutnya

Label (ground truth dari KAMU, bukan dari kode):
  TERSEGEL = kardus tertutup, tidak berisiko (diam / taruh pelan)
  BENTURAN = benturan sedang, perlu dicek
  TERBUKA  = kardus dibuka (cahaya naik) tanpa benturan keras
  RUSAK    = benturan keras / barang di dalam rusak

Setiap kali collect.py dijalankan = 1 "sesi" (kolom session). train_tree.py membagi data
latih/uji PER SESI supaya window yang mirip dari sesi yang sama tidak bocor ke data uji.
Window yang dicatat:
  BENTURAN/RUSAK : hanya window kejadian (puncak >= model_min_peak_g atau melayang)
  TERBUKA        : hanya window dengan cahaya >= --min-lux
  TERSEGEL       : semua window (pakai --every 2 untuk mengurangi data kembar)
"""
import argparse
import csv
import json
import os
import uuid

from shared import config as cfg

COLUMNS = ["session", "ts", "label", "note", "height_cm", "padding", "device_id",
           "impact_peak_g", "impact_count", "rotation_rad", "freefall",
           "lux_avg", "lux_delta", "rate_hz"]


def keep_window(label: str, d: dict, th, min_lux: float) -> bool:
    """Apakah window ini layak dicatat untuk label tersebut."""
    peak = float(d["impact_peak_g"])
    lux = d.get("lux_avg")
    if label in ("BENTURAN", "RUSAK"):
        return peak >= th.model_min_peak_g or bool(d.get("freefall"))
    if label == "TERBUKA":
        return lux is not None and float(lux) >= min_lux
    return True  # TERSEGEL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["TERSEGEL", "BENTURAN", "TERBUKA", "RUSAK"])
    ap.add_argument("--note", default="", help="mis. '30cm berbantalan'")
    ap.add_argument("--height-cm", default="", help="tinggi jatuh (opsional, untuk laporan)")
    ap.add_argument("--padding", default="", choices=["", "ya", "tidak"], help="kardus berbantalan?")
    ap.add_argument("--device", default="edge1")
    ap.add_argument("--out", default="data/drops.csv")
    ap.add_argument("--min-lux", type=float, default=1.0, help="batas cahaya untuk label TERBUKA")
    ap.add_argument("--every", type=int, default=1, help="catat tiap N window (untuk TERSEGEL)")
    args = ap.parse_args()

    th = cfg.Thresholds.from_env()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    new_file = not os.path.exists(args.out)
    f = open(args.out, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    if new_file:
        writer.writeheader()

    session = uuid.uuid4().hex[:8]
    state = {"n": 0, "seen": 0}
    topic = cfg.topic(f"edge/{args.device}/telemetry")

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe(topic, qos=1)
        print(f"[COLLECT] sesi={session} label={args.label} note='{args.note}' <- {topic}")
        print("[COLLECT] Lakukan skenario. Ctrl+C kalau sesi selesai.")

    def on_message(client, userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8", errors="replace"))
            float(d["impact_peak_g"])
        except (ValueError, KeyError):
            return
        if not keep_window(args.label, d, th, args.min_lux):
            return
        state["seen"] += 1
        if args.label == "TERSEGEL" and state["seen"] % max(1, args.every) != 0:
            return
        state["n"] += 1
        writer.writerow({
            "session": session, "ts": d.get("sent_at_ms"), "label": args.label, "note": args.note,
            "height_cm": args.height_cm, "padding": args.padding,
            "device_id": d.get("device_id", args.device), "impact_peak_g": d["impact_peak_g"],
            "impact_count": d.get("impact_count", 0), "rotation_rad": d.get("rotation_rad", 0.0),
            "freefall": int(bool(d.get("freefall"))),
            "lux_avg": "" if d.get("lux_avg") is None else d["lux_avg"],
            "lux_delta": "" if d.get("lux_delta") is None else d["lux_delta"],
            "rate_hz": d.get("rate_hz", ""),
        })
        f.flush()
        print(f"[COLLECT] #{state['n']} {args.label} peak={d['impact_peak_g']}g "
              f"count={d.get('impact_count')} freefall={d.get('freefall')} lux={d.get('lux_avg')}")

    client = cfg.make_client("collect")
    client.on_connect, client.on_message = on_connect, on_message
    client.connect(cfg.MQTT_HOST, cfg.MQTT_PORT, keepalive=30)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        f.close()
        print(f"[COLLECT] {state['n']} baris tersimpan di {args.out} (sesi {session})")


if __name__ == "__main__":
    main()
