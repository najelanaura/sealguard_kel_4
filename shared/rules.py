"""Aturan status paket. Dipisah dari MQTT supaya bisa di-test tanpa jaringan."""
from dataclasses import dataclass
from typing import Optional

from .config import Thresholds

# Makin tinggi makin parah. Dipakai untuk "latch": status hanya boleh naik.
SEVERITY = {"TERSEGEL": 0, "BENTURAN": 1, "TERBUKA": 2, "RUSAK": 3}


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str
    score: float  # indikator kasar 0..1, bukan probabilitas


class PacketJudge:
    def __init__(self, th: Optional[Thresholds] = None, model=None):
        self.th = th or Thresholds()
        self.model = model  # TreeModel atau None (None = pakai threshold)
        self.reset()

    def reset(self):
        self.current = Decision("TERSEGEL", "tersegel_aman", 0.0)
        self._last_impact_t = None

    def impact_level(self, p: dict) -> str:
        """Tingkat benturan saja (tanpa cahaya): TERSEGEL | BENTURAN | RUSAK.

        Kalau ada model, Decision Tree yang memutuskan, tetapi hanya untuk window yang
        lolos gerbang `model_min_peak_g`/melayang. Gerbang yang sama dipakai saat
        mengumpulkan data latih, jadi pohon tidak pernah melihat window "diam".
        """
        th = self.th
        peak = float(p.get("impact_peak_g", 0.0))
        count = int(p.get("impact_count", 0))
        freefall = bool(p.get("freefall", False))
        if self.model is not None:
            if peak >= th.model_min_peak_g or freefall:
                return self.model.predict(p)
            return "TERSEGEL"
        if peak >= th.rusak_g or (count >= th.rusak_count and peak >= th.rusak_count_g):
            return "RUSAK"
        if peak >= th.benturan_g or freefall:
            return "BENTURAN"
        return "TERSEGEL"

    def evaluate(self, p: dict, now: float) -> Decision:
        """Nilai SATU telemetry (tanpa latch). `now` dalam detik."""
        th = self.th
        peak = float(p.get("impact_peak_g", 0.0))
        lux = p.get("lux_avg")  # None untuk perangkat tanpa sensor cahaya (iPhone)

        level = self.impact_level(p)
        heavy = level == "RUSAK"
        impact = level != "TERSEGEL"
        if impact:
            self._last_impact_t = now
        recent_impact = (
            self._last_impact_t is not None and now - self._last_impact_t <= th.fusion_window_s
        )
        opened = lux is not None and float(lux) >= th.lux_open
        score = min(1.0, peak / (th.rusak_g * 1.2))

        if heavy:
            reason = "terbuka_akibat_jatuh" if opened else "benturan_keras"
            return Decision("RUSAK", reason, round(max(score, 0.8), 2))
        # fusion: benturan lalu cahaya naik = kardus pecah/terbuka akibat jatuh
        if opened and recent_impact:
            return Decision("RUSAK", "terbuka_akibat_jatuh", round(max(score, 0.8), 2))
        if opened:
            return Decision("TERBUKA", "dibuka_sengaja", round(max(score, 0.6), 2))
        if impact:
            return Decision("BENTURAN", "dibanting_masih_tersegel", round(score, 2))
        return Decision("TERSEGEL", "tersegel_aman", round(score, 2))

    def update(self, p: dict, now: float):
        """Nilai + latch. Return (status_sekarang, apakah_baru_naik)."""
        d = self.evaluate(p, now)
        escalated = SEVERITY[d.status] > SEVERITY[self.current.status]
        if escalated:
            self.current = d
        return self.current, escalated
