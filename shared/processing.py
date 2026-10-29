"""Pengolahan sensor per window. Tidak tahu apa-apa soal MQTT, jadi mudah di-test."""
from dataclasses import dataclass
from math import sqrt
from statistics import fmean
from typing import Optional, Sequence, Tuple

from .config import GRAVITY, Thresholds

Vec = Tuple[float, float, float]


@dataclass
class Sample:
    t: float                      # detik (time.monotonic)
    accel: Vec                    # m/s^2, sudah termasuk gravitasi
    gyro: Optional[Vec] = None    # rad/s
    lux: Optional[float] = None


def magnitude_g(v: Vec) -> float:
    return sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) / GRAVITY


def process_window(
    samples: Sequence[Sample],
    prev_lux_avg: Optional[float] = None,
    th: Thresholds = Thresholds(),
    new_after: Optional[float] = None,
) -> dict:
    """Ubah satu window sampel jadi angka ringkas yang dikirim ke MQTT.

    new_after: sampel dengan t <= new_after sudah dihitung di window sebelumnya
    (area overlap). Mereka hanya jadi konteks untuk deteksi melayang->mendarat;
    tidak ikut menambah impact_count dan rotation_rad supaya tidak terhitung dobel.
    """
    n = len(samples)
    mags = [magnitude_g(s.accel) for s in samples]
    peak = max(mags)

    # 1) jumlah benturan, dengan jeda minimal supaya satu benturan (beberapa
    #    sampel berturut-turut tinggi) tidak dihitung berkali-kali
    count, last_t = 0, float("-inf")
    for s, m in zip(samples, mags):
        if new_after is not None and s.t <= new_after:
            continue
        if m >= th.impact_count_g and s.t - last_t >= th.count_refractory_s:
            count += 1
            last_t = s.t

    # 2) melayang lalu mendarat: magnitude ~0 beberapa sampel, disusul lonjakan
    freefall, armed, run = False, False, 0
    for m in mags:
        if m < th.freefall_g:
            run += 1
            if run >= th.freefall_min_samples:
                armed = True
        else:
            run = 0
            if armed and m >= th.freefall_land_g:
                freefall = True

    # 3) total rotasi: integral kecepatan sudut
    rotation = 0.0
    for a, b in zip(samples, samples[1:]):
        if b.gyro is None or (new_after is not None and b.t <= new_after):
            continue
        dt = b.t - a.t
        if 0 < dt <= 0.5:
            rotation += sqrt(sum(c * c for c in b.gyro)) * dt

    duration = samples[-1].t - samples[0].t
    out = {
        "impact_peak_g": round(peak, 2),
        "impact_count": count,
        "rotation_rad": round(rotation, 2),
        "freefall": freefall,
        "samples": n,
        "rate_hz": round((n - 1) / duration, 1) if duration > 0 else 0.0,
    }

    # 4) cahaya (hanya kalau sensornya ada)
    lux = [s.lux for s in samples if s.lux is not None]
    if lux:
        avg = fmean(lux)
        out["lux_avg"] = round(avg, 1)
        out["lux_delta"] = round(avg - prev_lux_avg, 1) if prev_lux_avg is not None else 0.0
    return out
