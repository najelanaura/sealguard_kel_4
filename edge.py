#!/usr/bin/env python3
"""Edge device: baca sensor -> olah per window -> publish telemetry ke MQTT.

Contoh:
  python edge.py --device-id edge1                 # Android di dalam kardus (3 sensor)
  python edge.py --device-id edge2 --no-light      # HP kedua tanpa sensor cahaya
  python edge.py --device-id edge1 --source simulator   # latihan di laptop
"""
import argparse
import json
import shutil
import time

from shared import config as cfg
from shared.processing import process_window
from shared.sensors import SimulatorSensors, TermuxSensors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device-id", default="edge1")
    ap.add_argument("--source", choices=["auto", "termux", "simulator"], default="auto")
    ap.add_argument("--no-light", action="store_true", help="jangan baca sensor cahaya")
    args = ap.parse_args()

    source = args.source
    if source == "auto":  # di HP ada termux-sensor, di laptop tidak
        source = "termux" if shutil.which("termux-sensor") else "simulator"
    use_light = not args.no_light
    sensors = TermuxSensors(use_light) if source == "termux" else SimulatorSensors(use_light)

    th = cfg.Thresholds.from_env()
    tag = f"[{args.device_id.upper()}]"
    topic = cfg.topic(f"edge/{args.device_id}/telemetry")

    client = cfg.make_client(args.device_id)
    client.connect(cfg.MQTT_HOST, cfg.MQTT_PORT, keepalive=30)
    client.loop_start()
    print(f"{tag} source={source} light={use_light}")
    print(f"{tag} publish ke {topic}")

    seq, prev_lux, last_end, buf = 0, None, None, []
    try:
        for s in sensors.samples():
            buf.append(s)
            if s.t - buf[0].t < th.window_s:
                continue
            fields = process_window(buf, prev_lux, th, new_after=last_end)
            prev_lux = fields.get("lux_avg", prev_lux)
            last_end = buf[-1].t
            # Sisakan ekor window sebagai overlap: jatuh bebas (melayang -> mendarat)
            # bisa terbelah di batas window dan akan terlewat kalau window dipotong bersih.
            buf = [x for x in buf if x.t >= last_end - th.overlap_s]

            seq += 1
            payload = {
                "device_id": args.device_id,
                "sequence": seq,
                "sent_at_ms": int(time.time() * 1000),
                **fields,
            }
            client.publish(topic, json.dumps(payload), qos=1)
            print(
                f"{tag} #{seq} peak={fields['impact_peak_g']}g count={fields['impact_count']} "
                f"rot={fields['rotation_rad']} freefall={fields['freefall']} "
                f"lux={fields.get('lux_avg', '-')} rate={fields['rate_hz']}Hz"
            )
    except KeyboardInterrupt:
        pass
    finally:
        sensors.close()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
