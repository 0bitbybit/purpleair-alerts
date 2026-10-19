"""Entry point: fetch → correct → evaluate rules → notify → save state.

    python -m aqalert --config config.yaml --state state.json
    python -m aqalert --demo tests/fixtures/smoky_hot_day.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import purpleair
from .aqi import aqi_category, combine_channels, correct_pm25, pm25_to_aqi
from .heat import heat_index_f
from .notify import Notifier
from .rules import Hazard, HazardState, Level, evaluate

log = logging.getLogger("aqalert")

KIND_WORD = {
    "escalation": "ALERT",
    "downgrade": "UPDATE",
    "all_clear": "ALL CLEAR",
    "reminder": "REMINDER",
}


SMS_SAFE = str.maketrans({"—": "-", "§": "Sec ", "µ": "u", "°": ""})


def to_sms(text: str, limit: int = 300) -> str:
    """Keep texts in the plain GSM-7 character set; one non-GSM character
    switches the whole message to a format with 70-character segments."""
    out = text.translate(SMS_SAFE).encode("ascii", "ignore").decode().strip()
    return out if len(out) <= limit else out[: limit - 3] + "..."


def build_hazards(cfg: dict) -> dict[str, Hazard]:
    def mk(key, label, unit, section):
        return Hazard(
            key=key,
            label=label,
            unit=unit,
            levels=[Level(**lv) for lv in section["levels"]],
            deadband=float(section.get("deadband", 0)),
            reminder_hours=float(section.get("reminder_hours", 4)),
        )

    heat_cfg = cfg["heat"]
    heat_label = "Heat index" if heat_cfg.get("metric") == "heat_index" else "Temperature"
    return {
        "smoke": mk("smoke", "Smoke (PM2.5 AQI)", "AQI", cfg["smoke"]),
        "heat": mk("heat", heat_label, "°F", heat_cfg),
        "sensor": Hazard(
            "sensor", "Sensor health", "", [Level("Sensor needs attention", 1)], 0, 24
        ),
    }


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            log.warning("State file unreadable; starting fresh")
    return {}


def run(cfg: dict, readings, state: dict, notifier: Notifier, now: float) -> dict:
    hazards = build_hazards(cfg)
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    stamp = datetime.fromtimestamp(now, tz).strftime("%a %b %d, %I:%M %p %Z")
    heat_cfg = cfg["heat"]
    stale_after = float(cfg.get("stale_after_minutes", 30))
    site = cfg.get("site_name", "Site")

    for r, sensor_cfg in readings:
        label = sensor_cfg.get("label") or r.name
        sstate = state.setdefault(str(r.sensor_index), {})
        map_link = f"https://map.purpleair.com/?select={r.sensor_index}"

        # --- derive values -------------------------------------------------
        problems = []
        if r.age_minutes > stale_after:
            problems.append(f"no data for {r.age_minutes:.0f} min")
        pm = combine_channels(r.pm25_cf1_a, r.pm25_cf1_b)
        if not pm.ok:
            problems.append(pm.note)

        values: dict[str, float] = {}
        details: list[str] = []
        stale = r.age_minutes > stale_after

        # Skip smoke when the two laser counters disagree: the smoke state is held,
        # and the sensor-health alert below tells someone to check the sensor.
        if not stale and pm.usable and r.humidity is not None:
            # The EPA correction was fit on PurpleAir's raw humidity, so use it here.
            pm_corr = correct_pm25(pm.value, r.humidity)
            values["smoke"] = pm25_to_aqi(pm_corr)
            details.append(
                f"PM2.5 AQI {values['smoke']:.0f} ({aqi_category(int(values['smoke']))}), "
                f"PM2.5 {pm_corr:.1f} µg/m³ (EPA-corrected)"
            )
        if not stale and r.temperature_f is not None and r.humidity is not None:
            t = r.temperature_f + float(heat_cfg.get("temperature_offset_f", -8))
            rh = min(100.0, r.humidity + float(heat_cfg.get("humidity_offset", 4)))
            hi = heat_index_f(t, rh)
            values["heat"] = hi if heat_cfg.get("metric") == "heat_index" else t
            details.append(f"Temp {t:.0f} °F, RH {rh:.0f}%, heat index {hi:.0f} °F")
        values["sensor"] = 1.0 if problems else 0.0

        # --- evaluate each hazard -------------------------------------------
        for key, value in values.items():
            hz = hazards[key]
            prev = HazardState.from_dict(sstate.get(key))
            d = evaluate(value, hz, prev, now)
            sstate[key] = d.new_state.to_dict()
            if not d.kind:
                continue

            word = KIND_WORD[d.kind]
            if key == "sensor":
                headline = (
                    f"{label}: sensor back to normal"
                    if d.kind == "all_clear"
                    else f"{label}: sensor problem — {'; '.join(problems)}"
                )
                action = "Check power/Wi-Fi and the sensor's A/B channels." if problems else ""
            else:
                lvl = d.level.name if d.level else "Normal"
                shown = f"{value:.0f} {hz.unit}".strip()
                headline = f"{label}: {hz.label} — {lvl} ({shown})"
                action = d.level.action if d.level else "Conditions back below all alert levels."

            title = f"[{site}] {word}: {headline}"
            body_lines = [headline, "", *details]
            if problems and key != "sensor":
                body_lines.append(f"Data quality note: {'; '.join(problems)}")
            if action:
                body_lines += ["", f"Action: {action}"]
            body_lines += ["", f"As of {stamp}", map_link]
            short = to_sms(f"{word} {headline}. {action}")

            log.info("%s", title)
            notifier.send(title, "\n".join(body_lines), short[:300])

    return state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PurpleAir heat + wildfire smoke alerts")
    p.add_argument("--config", help="default: config.yaml (config.example.yaml with --demo)")
    p.add_argument("--state", default="state.json")
    p.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    p.add_argument("--demo", metavar="FIXTURE", help="use canned readings instead of the API")
    p.add_argument("--test-notify", action="store_true",
                   help="send one test email/SMS to confirm delivery, then exit")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.test_notify:
        ok = Notifier(dry_run=args.dry_run).send(
            "[aqalert] Test notification",
            "If you can read this, email alerts are working.",
            "aqalert test: SMS alerts are working.",
        )
        print("Test sent." if ok else "Test failed - check EMAIL_URLS / SMS_URLS.")
        return 0 if ok else 1

    # The demo fixtures belong to the example sensors, so --demo uses the example config
    # unless told otherwise.
    cfg_path = Path(args.config or ("config.example.yaml" if args.demo else "config.yaml"))
    if not cfg_path.exists():
        log.error("Config file %s not found (copy config.example.yaml to config.yaml)", cfg_path)
        return 2
    cfg = yaml.safe_load(cfg_path.read_text())

    failures = 0
    if args.demo:
        demo = {r.sensor_index: r for r in purpleair.load_demo(args.demo)}
        readings = [(demo[s["index"]], s) for s in cfg["sensors"] if s["index"] in demo]
        if not readings:
            log.error("None of the sensors in %s are in the demo data %s", cfg_path, args.demo)
            return 2
    else:
        api_key = os.environ.get("PURPLEAIR_API_KEY")
        if not api_key:
            log.error("PURPLEAIR_API_KEY is not set")
            return 2
        readings = []
        for s in cfg["sensors"]:
            read_key = os.environ.get(s["read_key_env"]) if s.get("read_key_env") else None
            try:
                readings.append((purpleair.fetch(s["index"], api_key, read_key), s))
            except Exception as e:  # keep going for the other sensors
                log.error("Fetch failed for sensor %s: %s", s["index"], e)
                failures += 1

    state_path = Path(args.state)
    state = load_state(state_path)
    notifier = Notifier(dry_run=args.dry_run)
    state = run(cfg, readings, state, notifier, time.time())
    log.info("Checked %d sensor(s); %d alert(s) sent", len(readings), notifier.count)
    state_path.write_text(json.dumps(state, indent=2))
    # A non-zero exit marks the GitHub Actions run as failed, which emails the repo owner.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
