"""Konfigurasi dari .env. Semua ambang bisa diubah tanpa menyentuh kode."""
import os
import uuid
from dataclasses import dataclass, fields

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # opsional, supaya unit test jalan tanpa install apa pun
    pass

GRAVITY = 9.81
PROJECT_ID = os.getenv("PROJECT_ID", "sealguard_demo")
MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MODEL_PATH = os.getenv("MODEL_PATH", "model.json")
MQTT_TLS = os.getenv("MQTT_TLS", "true").strip().lower() in ("1", "true", "yes", "y")


@dataclass(frozen=True)
class Thresholds:
    window_s: float = 1.0            # panjang window pengolahan
    overlap_s: float = 0.3           # sisa window lama dibawa ke window baru (lihat edge.py)
    impact_count_g: float = 2.0      # magnitude >= ini dihitung 1 benturan
    count_refractory_s: float = 0.3  # jeda minimal antar hitungan benturan
    freefall_g: float = 0.3          # magnitude < ini dianggap melayang
    freefall_min_samples: int = 3    # minimal sampel berturut-turut untuk "melayang"
    freefall_land_g: float = 1.5     # lonjakan setelah melayang = mendarat
    model_min_peak_g: float = 1.3    # window dengan peak >= ini (atau melayang) diserahkan ke Decision Tree
    benturan_g: float = 2.0
    rusak_g: float = 5.0
    rusak_count: int = 3
    rusak_count_g: float = 3.0
    lux_open: float = 20.0           # lux >= ini = kardus terbuka
    fusion_window_s: float = 5.0     # benturan + lux naik dalam rentang ini = terbuka akibat jatuh

    @classmethod
    def from_env(cls):
        d = cls()
        values = {}
        for f in fields(d):
            default = getattr(d, f.name)
            values[f.name] = type(default)(os.getenv(f.name.upper(), default))
        return cls(**values)


def topic(suffix: str) -> str:
    return f"sealguard/{PROJECT_ID}/{suffix}"


def make_client(name: str):
    import paho.mqtt.client as mqtt

    # client_id dibuat acak: kalau dua klien memakai id sama, broker memutus salah satunya.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"sealguard-{name}-{uuid.uuid4().hex[:8]}",
    )
    if MQTT_TLS:
        client.tls_set()
    return client
