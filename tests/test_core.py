import json
from pathlib import Path

import pytest
import yaml

from aqalert.aqi import aqi_category, combine_channels, correct_pm25, pm25_to_aqi
from aqalert.heat import heat_index_f
from aqalert.main import run
from aqalert import purpleair
from aqalert.rules import Hazard, HazardState, Level, evaluate

ROOT = Path(__file__).resolve().parents[1]


# --- AQI -------------------------------------------------------------------
@pytest.mark.parametrize(
    "conc,aqi",
    [(0.0, 0), (9.0, 50), (9.1, 51), (35.4, 100), (35.5, 101), (55.4, 150),
     (55.5, 151), (125.4, 200), (225.4, 300), (325.4, 500)],
)
def test_aqi_breakpoints(conc, aqi):
    assert pm25_to_aqi(conc) == aqi


def test_aqi_beyond_scale_extrapolates():
    assert pm25_to_aqi(400) > 500
    assert aqi_category(pm25_to_aqi(400)) == "Beyond the AQI"


def test_correction_is_continuous_at_segment_edges():
    for edge in (30, 50, 210, 260):
        lo, hi = correct_pm25(edge - 1e-6, 40), correct_pm25(edge + 1e-6, 40)
        assert abs(lo - hi) < 0.01


def test_channels():
    assert combine_channels(10, 11).ok
    assert not combine_channels(12, 80).ok
    assert combine_channels(None, 7).value == 7
    assert combine_channels(None, 7).usable
    assert not combine_channels(12, 80).usable


# --- Heat index ------------------------------------------------------------
@pytest.mark.parametrize("t,rh,expected", [(90, 50, 95), (100, 40, 110), (80, 40, 80)])
def test_heat_index_matches_nws_table(t, rh, expected):
    assert abs(heat_index_f(t, rh) - expected) <= 1.5


# --- Rules -----------------------------------------------------------------
HZ = Hazard("smoke", "Smoke", "AQI", [Level("USG", 101), Level("Trigger", 151)], deadband=5, reminder_hours=4)


def test_escalation_then_quiet_then_reminder():
    d = evaluate(160, HZ, HazardState(), now=0)
    assert d.kind == "escalation" and d.new_state.level == 2
    d2 = evaluate(165, HZ, d.new_state, now=600)
    assert d2.kind is None
    d3 = evaluate(165, HZ, d2.new_state, now=4 * 3600)
    assert d3.kind == "reminder"


def test_deadband_prevents_flapping():
    s = HazardState(level=2, last_notified=0)
    assert evaluate(148, HZ, s, now=60).kind is None      # inside deadband: hold
    assert evaluate(140, HZ, s, now=60).kind == "downgrade"


def test_all_clear():
    s = HazardState(level=1, last_notified=0)
    assert evaluate(40, HZ, s, now=60).kind == "all_clear"


# --- End to end with demo data ----------------------------------------------
class Capture:
    def __init__(self):
        self.sent = []

    def send(self, title, body, short):
        self.sent.append((title, body, short))
        return True


def _run(fixture, state, now):
    cfg = yaml.safe_load((ROOT / "config.example.yaml").read_text())
    demo = {r.sensor_index: r for r in purpleair.load_demo(ROOT / "tests/fixtures" / fixture)}
    readings = [(demo[s["index"]], s) for s in cfg["sensors"]]
    cap = Capture()
    state = run(cfg, readings, state, cap, now)
    return state, cap.sent


def test_demo_day_alerts_then_clears():
    import time

    now = time.time()
    state, sent = _run("smoky_hot_day.json", {}, now)
    titles = " | ".join(t for t, _, _ in sent)
    assert "Wildfire smoke rule trigger" in titles
    assert "High heat procedures" in titles
    assert "sensor problem" in titles            # yard A/B disagreement
    assert all(len(s) <= 300 and s.isascii() for _, _, s in sent)
    # yard's disagreeing channels must not produce a smoke alert
    assert not any("Shipping yard" in t and "Smoke" in t for t, _, _ in sent)

    # same conditions 10 min later: nothing new
    state, sent = _run("smoky_hot_day.json", json.loads(json.dumps(state)), now + 600)
    assert sent == []

    # conditions clear
    state, sent = _run("clear_day.json", state, now + 1200)
    assert any("ALL CLEAR" in t for t, _, _ in sent)
