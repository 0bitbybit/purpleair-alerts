"""Minimal PurpleAir API v1 client (replaces the Power Automate HTTP action)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

API_URL = "https://api.purpleair.com/v1/sensors/{index}"

# Keep the field list short: PurpleAir bills API points per field requested.
FIELDS = [
    "name",
    "last_seen",
    "temperature",
    "humidity",
    "pm2.5_cf_1_a",
    "pm2.5_cf_1_b",
]


@dataclass
class Reading:
    sensor_index: int
    name: str
    last_seen: int  # unix seconds
    temperature_f: float | None  # raw, as reported (inside the sensor housing)
    humidity: float | None  # raw, as reported
    pm25_cf1_a: float | None
    pm25_cf1_b: float | None

    @property
    def age_minutes(self) -> float:
        return (time.time() - self.last_seen) / 60


def _parse(index: int, sensor: dict) -> Reading:
    return Reading(
        sensor_index=index,
        name=sensor.get("name", f"sensor {index}"),
        last_seen=int(sensor.get("last_seen", 0)),
        temperature_f=sensor.get("temperature"),
        humidity=sensor.get("humidity"),
        pm25_cf1_a=sensor.get("pm2.5_cf_1_a"),
        pm25_cf1_b=sensor.get("pm2.5_cf_1_b"),
    )


def fetch(index: int, api_key: str, read_key: str | None = None, timeout: int = 20) -> Reading:
    params = {"fields": ",".join(FIELDS)}
    if read_key:  # only needed for private sensors
        params["read_key"] = read_key
    resp = requests.get(
        API_URL.format(index=index),
        headers={"X-API-Key": api_key},
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    return _parse(index, resp.json()["sensor"])


def load_demo(path: str | Path) -> list[Reading]:
    """Load canned readings so the project can be demoed without an API key.
    ``last_seen`` of 0 in the fixture is replaced with 'now'."""
    data = json.loads(Path(path).read_text())
    out = []
    for item in data:
        sensor = dict(item["sensor"])
        if not sensor.get("last_seen"):
            sensor["last_seen"] = int(time.time())
        out.append(_parse(item["sensor_index"], sensor))
    return out
