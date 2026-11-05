#!/usr/bin/env python3
"""Kumpulkan data latih: catat telemetry edge1 ke CSV beserta label dari kamu.

Alur per batch percobaan (detector.py TIDAK perlu jalan):
  1. python edge.py --device-id edge1            # di HP dalam kardus
  2. python collect.py --label RUSAK --note "50cm tanpa bantalan"   # di laptop
  3. jatuhkan kardus sesuai skenario, ulangi 10-15 kali, Ctrl+C
  4. ganti --label / --note, ulangi untuk skenario berikutnya

Label (ground truth dari KAMU, bukan dari kode):
  TERSEGEL = aman, tidak berisiko     BENTURAN = perlu dicek     RUSAK = barang di dalam rusak
"""
import argparse
import csv
import json
import os

from shared import config as cfg

COLUMNS = ["ts", "label", "note", "device_id", "impact_peak_g", "impact_count",
           "rotation_rad", "freefall", "rate_hz"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, choices=["TERSEGEL", "BENTURAN", "RUSAK"])
    ap.add_argument("--note", default="", help="mis. '30cm berbantalan'")
    ap.add_argument("--device", default="edge1")
    ap.add_argument("--out", default="data/drops.csv")
    args = ap.parse_args()

    th = cfg.Thresholds.from_env()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    new_file = not os.path.exists(args.out)
    f = open(args.out, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    if new_file:
        writer.writeheader()

    state = {"last_seq": None, "n": 0}
    topic = cfg.topic(f"edge/{args.device}/telemetry")

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe(topic, qos=1)
        print(f"[COLLECT] label={args.label} note='{args.note}' <- {topic}")
        print("[COLLECT] Jatuhkan kardus. Ctrl+C kalau batch selesai.")

    def on_message(client, userdata, msg):
        try:
            d = json.loads(msg.payload.decode("utf-8", errors="replace"))
            peak = float(d["impact_peak_g"])
        except (ValueError, KeyError):
            return
        # Gerbang yang sama dengan detector: window "diam" bukan kejadian.
        if not (peak >= th.model_min_peak_g or d.get("freefall")):
            return
        seq = d.get("sequence")
        # Window berikutnya hampir pasti memuat ekor kejadian yang sama (overlap) -> lewati.
        if seq is not None and state["last_seq"] is not None and seq == state["last_seq"] + 1:
            return
        state["last_seq"] = seq
        state["n"] += 1
        writer.writerow({
            "ts": d.get("sent_at_ms"), "label": args.label, "note": args.note,
            "device_id": d.get("device_id", args.device), "impact_peak_g": peak,
            "impact_count": d.get("impact_count", 0), "rotation_rad": d.get("rotation_rad", 0.0),
            "freefall": int(bool(d.get("freefall"))), "rate_hz": d.get("rate_hz", ""),
        })
        f.flush()
        print(f"[COLLECT] #{state['n']} {args.label} peak={peak}g count={d.get('impact_count')} "
              f"freefall={d.get('freefall')}")

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
        print(f"[COLLECT] {state['n']} baris tersimpan di {args.out}")


if __name__ == "__main__":
    main()
