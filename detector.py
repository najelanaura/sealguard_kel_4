#!/usr/bin/env python3
"""Detector (laptop): subscribe telemetry -> nilai status paket -> publish status & alert.

Kirim RESET ke topic control untuk mengosongkan status (status sengaja di-latch):
  mosquitto_pub -h broker.hivemq.com -p 8883 --capath /etc/ssl/certs \
      -t "sealguard/<PROJECT_ID>/control" -m RESET
(atau lewat tombol RESET di dashboard nanti)
"""
import json
import os
import time
import traceback

from shared import config as cfg
from shared.rules import PacketJudge
from shared.tree_model import TreeModel


def normalize(data: dict):
    """Samakan format telemetry. Return None kalau tidak dikenali."""
    if "impact_peak_g" in data:  # format SealGuard (edge.py)
        return data
    # Format halaman iot.myudak.com: hanya snapshot 2 detik sekali, jadi puncak benturan
    # hampir pasti terlewat. Cukup untuk "HP ini bergerak/tidak", bukan untuk impact.
    acc = (data.get("sensors") or {}).get("accelerometer") or {}
    if "magnitude" in acc:
        return {
            "impact_peak_g": round(float(acc["magnitude"]) / cfg.GRAVITY, 2),
            "impact_count": 0,
            "freefall": False,
            "rotation_rad": 0.0,
            "source": "web_snapshot",
        }
    return None


class Detector:
    def __init__(self):
        model = TreeModel.load(cfg.MODEL_PATH) if os.path.exists(cfg.MODEL_PATH) else None
        self.method = "tree" if model else "threshold"
        print(f"[DETECTOR] Metode benturan: {'Decision Tree (' + cfg.MODEL_PATH + ')' if model else 'threshold (model.json tidak ditemukan)'}")
        self.judge = PacketJudge(cfg.Thresholds.from_env(), model)
        self.latest = {}    # device_id -> telemetry terakhir (sudah dinormalisasi)
        self.peak_max = {}  # device_id -> impact tertinggi sejak RESET
        self.client = cfg.make_client("detector")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[DETECTOR] Terhubung ke {cfg.MQTT_HOST}:{cfg.MQTT_PORT} ({reason_code})")
        client.subscribe([(cfg.topic("edge/+/telemetry"), 1), (cfg.topic("control"), 1)])
        print(f"[CONFIG] PROJECT_ID: {cfg.PROJECT_ID}")
        print("[DETECTOR] Menunggu telemetry dari edge1 (kardus) dan edge2 (pembanding)...")

    def on_message(self, client, userdata, msg):
        try:
            raw = msg.payload.decode("utf-8", errors="replace").strip()
            if msg.topic == cfg.topic("control"):
                self.handle_control(raw)
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return
            device = msg.topic.split("/")[-2]
            norm = normalize(data)
            if norm is None:
                return
            if device not in self.latest:
                print(f"[DETECTOR] {device} terhubung ({norm.get('source', 'sealguard')})")
            self.latest[device] = norm
            self.peak_max[device] = max(self.peak_max.get(device, 0.0), float(norm["impact_peak_g"]))

            if device != "edge1":  # edge2 hanya pembanding, tidak menentukan status
                return
            decision, escalated = self.judge.update(norm, time.monotonic())
            if escalated:
                print(f"[STATUS] {decision.status} ({decision.reason}) peak={norm['impact_peak_g']}g")
                if decision.status in ("RUSAK", "TERBUKA"):
                    alert = {
                        "status": decision.status,
                        "reason": decision.reason,
                        "score": decision.score,
                        "decided_at_ms": int(time.time() * 1000),
                    }
                    client.publish(cfg.topic("alerts"), json.dumps(alert), qos=1)
            self.publish_status()
        except Exception:
            traceback.print_exc()

    def handle_control(self, raw: str):
        cmd = raw.upper()
        if raw.startswith("{"):
            try:
                cmd = str(json.loads(raw).get("cmd", "")).upper()
            except json.JSONDecodeError:
                return
        if cmd == "RESET":
            self.judge.reset()
            self.peak_max.clear()
            print("[STATUS] RESET -> TERSEGEL")
            self.publish_status()

    def status_payload(self) -> dict:
        d = self.judge.current
        e1, e2 = self.latest.get("edge1", {}), self.latest.get("edge2", {})
        p1, p2 = self.peak_max.get("edge1"), self.peak_max.get("edge2")
        # Persentase peredaman bantalan: hanya masuk akal kalau edge2 (tanpa bantalan)
        # dijatuhkan dengan cara yang sama dengan edge1 (berbantalan).
        reduction = round((1 - p1 / p2) * 100, 1) if p1 and p2 and p2 > 1.5 else None
        payload = {
            "status": d.status,
            "reason": d.reason,
            "score": d.score,
            "method": self.method,
            "peak_max_g": p1 or 0.0,
            "edge1": {"impact_peak_g": e1.get("impact_peak_g"), "lux_avg": e1.get("lux_avg")},
            "edge2": {"impact_peak_g": e2.get("impact_peak_g")},
            "padding_reduction_pct": reduction,
            "thresholds": {"benturan_g": self.judge.th.benturan_g, "rusak_g": self.judge.th.rusak_g},
            "decided_at_ms": int(time.time() * 1000),
        }
        return payload

    def publish_status(self):
        payload = self.status_payload()
        self.client.publish(cfg.topic("status"), json.dumps(payload), qos=1, retain=True)

    def run(self):
        self.client.connect(cfg.MQTT_HOST, cfg.MQTT_PORT, keepalive=30)
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.client.disconnect()


if __name__ == "__main__":
    Detector().run()
