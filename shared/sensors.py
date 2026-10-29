"""Sumber sensor: HP Android (Termux:API) atau simulator (laptop)."""
import json
import random
import subprocess
import time
from typing import Iterator, Optional

from .config import GRAVITY
from .processing import Sample


def _pick(obj: dict, include: str):
    """Ambil 'values' dari sensor yang namanya memuat `include` (abaikan Uncalibrated)."""
    for key, val in obj.items():
        k = key.lower()
        if include in k and "uncalibrated" not in k and isinstance(val, dict):
            values = val.get("values")
            if values:
                return values
    return None


class TermuxSensors:
    """Membaca termux-sensor sebagai STREAM (satu proses terus hidup).

    Kenapa bukan `-n 1` per sampel seperti contoh dosen? Setiap panggilan
    memulai proses baru (~ratusan ms), jadi hanya dapat beberapa sampel per
    detik dan puncak benturan (hitungan milidetik) hampir pasti terlewat.
    """

    def __init__(self, use_light: bool = True, delay_ms: int = 20):
        names = ["accelerometer", "gyroscope"] + (["light"] if use_light else [])
        self.cmd = ["termux-sensor", "-s", ",".join(names), "-d", str(delay_ms)]
        self.proc: Optional[subprocess.Popen] = None

    def samples(self) -> Iterator[Sample]:
        self.proc = subprocess.Popen(
            self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        decoder = json.JSONDecoder()
        buf, gyro, lux = "", None, None
        got_any = False
        try:
            for line in self.proc.stdout:
                buf += line
                while True:  # output berupa objek JSON bersambung-sambung
                    buf = buf.lstrip()
                    if not buf:
                        break
                    try:
                        obj, end = decoder.raw_decode(buf)
                    except json.JSONDecodeError:
                        break  # objek belum lengkap, baca baris berikutnya
                    buf = buf[end:]
                    g = _pick(obj, "gyroscope")
                    if g:
                        gyro = tuple(float(v) for v in g[:3])
                    lv = _pick(obj, "light")
                    if lv:
                        lux = float(lv[0])
                    a = _pick(obj, "accelerometer")
                    if a:
                        got_any = True
                        yield Sample(time.monotonic(), tuple(float(v) for v in a[:3]), gyro, lux)
        finally:
            self.close()
        hint = "" if got_any else " Tidak ada data: izinkan Termux:API di pengaturan Android."
        raise RuntimeError("termux-sensor berhenti." + hint)

    def close(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class SimulatorSensors:
    """Simulasi satu siklus 24 detik supaya detector bisa dilatih tanpa HP.

      4.0 s  jatuh pelan (~2,6 g)     -> BENTURAN
     12-16 s kardus dibuka (90 lux)   -> TERBUKA (dibuka_sengaja)
     19.0 s  jatuh keras (~6,5 g)     -> RUSAK
    Status di-latch, jadi kirim RESET di antara skenario kalau mau mengulang.
    """

    CYCLE_S = 24.0
    # (mulai, selesai, magnitude g, kecepatan sudut rad/s)
    EVENTS = [
        (4.00, 4.15, 0.05, 0.4), (4.15, 4.20, 2.6, 1.5),
        (19.00, 19.15, 0.05, 0.5), (19.15, 19.20, 6.5, 4.0), (19.20, 19.30, 2.5, 2.0),
    ]
    LUX_OPEN = (12.0, 16.0, 90.0)

    def __init__(self, use_light: bool = True, rate_hz: int = 50):
        self.use_light = use_light
        self.dt = 1.0 / rate_hz

    def samples(self) -> Iterator[Sample]:
        t0 = time.monotonic()
        while True:
            now = time.monotonic()
            t = (now - t0) % self.CYCLE_S
            mag, omega = 1.0, 0.02
            for start, end, m, w in self.EVENTS:
                if start <= t < end:
                    mag, omega = m, w
            lux = None
            if self.use_light:
                lux = self.LUX_OPEN[2] if self.LUX_OPEN[0] <= t < self.LUX_OPEN[1] else 0.2
            n = lambda: random.gauss(0, 0.05)
            yield Sample(now, (n(), n(), mag * GRAVITY + n()), (omega, 0.0, 0.0), lux)
            time.sleep(self.dt)

    def close(self):
        pass
