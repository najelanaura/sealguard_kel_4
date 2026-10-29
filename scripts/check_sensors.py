#!/usr/bin/env python3
"""Cek sensor HP Android (Termux:API) sebelum menjalankan edge.py.

  python -m scripts.check_sensors              # cek accelerometer + gyroscope + light
  python -m scripts.check_sensors --no-light   # HP tanpa sensor cahaya
  python -m scripts.check_sensors --seconds 5  # lama pengukuran
"""
import argparse
import shutil
import sys
import time

from shared.processing import magnitude_g
from shared.sensors import TermuxSensors


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-light", action="store_true", help="jangan cek sensor cahaya")
    ap.add_argument("--seconds", type=float, default=3.0, help="lama pengukuran (detik)")
    args = ap.parse_args(argv)

    if not shutil.which("termux-sensor"):
        print("[GAGAL] perintah 'termux-sensor' tidak ditemukan.")
        print("  - Jalankan di HP Android lewat Termux (bukan di laptop).")
        print("  - Pasang: pkg install termux-api, lalu pasang aplikasi Termux:API dari F-Droid.")
        return 1

    use_light = not args.no_light
    sensors = TermuxSensors(use_light)
    print(f"Membaca sensor selama {args.seconds:g} detik... (diamkan HP di meja)")
    n, mags, gyro_seen, lux_seen = 0, [], False, False
    t0 = time.monotonic()
    try:
        for s in sensors.samples():
            n += 1
            mags.append(magnitude_g(s.accel))
            gyro_seen = gyro_seen or s.gyro is not None
            lux_seen = lux_seen or s.lux is not None
            if time.monotonic() - t0 >= args.seconds:
                break
    except RuntimeError as e:
        print(f"[GAGAL] {e}")
        return 1
    finally:
        sensors.close()

    if n == 0:
        print("[GAGAL] tidak ada sampel yang terbaca.")
        return 1

    elapsed = max(time.monotonic() - t0, 1e-6)
    rate = n / elapsed
    avg = sum(mags) / n
    print(f"[OK]  accelerometer: {n} sampel, ~{rate:.0f} Hz, rata-rata {avg:.2f} g (diam di meja = sekitar 1.0 g)")
    print(f"[{'OK' if gyro_seen else 'GAGAL'}]  gyroscope: {'terbaca' if gyro_seen else 'tidak terbaca'}")
    if use_light:
        print(f"[{'OK' if lux_seen else 'GAGAL'}]  light: {'terbaca' if lux_seen else 'tidak terbaca (coba --no-light)'}")

    ok = gyro_seen and (lux_seen or not use_light)
    if rate < 20:
        print("[PERINGATAN] sampling rendah (< 20 Hz): puncak benturan bisa terlewat. Tutup aplikasi lain.")
    if not 0.8 <= avg <= 1.2:
        print("[PERINGATAN] rata-rata magnitude jauh dari 1 g: pastikan HP diam saat dicek.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
