"""PM2.5 correction and US EPA Air Quality Index.

* PurpleAir correction: EPA's US-wide correction (Barkjohn et al., 2021) with the
  2023 extension for high smoke concentrations, as used on the AirNow Fire and
  Smoke Map. Input is the A/B-averaged ``pm2.5_cf_1`` value.
* AQI: EPA breakpoints as revised in February 2024 (Good now tops out at 9.0 µg/m³).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# (C_low, C_high, I_low, I_high) — 24-hr PM2.5, µg/m³, EPA 2024 revision
PM25_BREAKPOINTS = [
    (0.0, 9.0, 0, 50),
    (9.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 125.4, 151, 200),
    (125.5, 225.4, 201, 300),
    (225.5, 325.4, 301, 500),
]

CATEGORIES = [
    (50, "Good"),
    (100, "Moderate"),
    (150, "Unhealthy for Sensitive Groups"),
    (200, "Unhealthy"),
    (300, "Very Unhealthy"),
    (500, "Hazardous"),
]


@dataclass
class ChannelCheck:
    value: float | None
    ok: bool
    note: str = ""
    usable: bool = True  # False when channels disagree: don't alert on that data


def combine_channels(a: float | None, b: float | None) -> ChannelCheck:
    """Average the A and B laser counters and flag when they disagree.

    Uses the EPA data-cleaning rule: the channels are considered in agreement when
    they differ by < 5 µg/m³ OR by < 70% relative percent difference.
    """
    vals = [v for v in (a, b) if v is not None and not math.isnan(v)]
    if not vals:
        return ChannelCheck(None, False, "no PM2.5 data", usable=False)
    if len(vals) == 1:
        return ChannelCheck(vals[0], False, "only one channel reporting")
    mean = (a + b) / 2
    diff = abs(a - b)
    rpd = diff / mean if mean else 0.0
    if diff < 5 or rpd < 0.70:
        return ChannelCheck(mean, True)
    return ChannelCheck(
        mean, False, f"A/B channels disagree (A={a:.1f}, B={b:.1f})", usable=False
    )


def correct_pm25(pa_cf1: float, rh: float) -> float:
    """EPA-corrected PM2.5 (µg/m³) from PurpleAir cf_1 data and relative humidity (%)."""
    pa = max(pa_cf1, 0.0)
    if pa < 30:
        v = 0.524 * pa - 0.0862 * rh + 5.75
    elif pa < 50:
        w = pa / 20 - 3 / 2
        v = (0.786 * w + 0.524 * (1 - w)) * pa - 0.0862 * rh + 5.75
    elif pa < 210:
        v = 0.786 * pa - 0.0862 * rh + 5.75
    elif pa < 260:
        w = pa / 50 - 21 / 5
        v = (
            (0.69 * w + 0.786 * (1 - w)) * pa
            - 0.0862 * rh * (1 - w)
            + 2.966 * w
            + 5.75 * (1 - w)
            + 8.84e-4 * pa**2 * w
        )
    else:
        v = 2.966 + 0.69 * pa + 8.84e-4 * pa**2
    return max(v, 0.0)


def pm25_to_aqi(conc: float) -> int:
    """Convert a PM2.5 concentration to AQI. Values above 325.4 are extrapolated
    ("Beyond the AQI") so that >500 thresholds can still be evaluated."""
    c = math.floor(max(conc, 0.0) * 10) / 10  # EPA truncates to 0.1
    for c_lo, c_hi, i_lo, i_hi in PM25_BREAKPOINTS:
        if c <= c_hi:
            return round((i_hi - i_lo) / (c_hi - c_lo) * (c - c_lo) + i_lo)
    c_lo, c_hi, i_lo, i_hi = PM25_BREAKPOINTS[-1]
    return round((i_hi - i_lo) / (c_hi - c_lo) * (c - c_lo) + i_lo)


def aqi_category(aqi: int) -> str:
    for upper, name in CATEGORIES:
        if aqi <= upper:
            return name
    return "Beyond the AQI"
