"""Alert rules (replaces the Power Automate 'Condition' steps).

Each hazard has an ordered list of thresholds. The *level* is how many thresholds
the current value meets. Notifications fire on change, not on every run:

* escalation  — level went up
* downgrade   — level went down but is still above 0
* all clear   — level went back to 0
* reminder    — level unchanged and > 0, and ``reminder_hours`` have passed

A deadband (hysteresis) keeps a reading that hovers around a threshold from
flapping: to drop a level, the value must fall below ``threshold - deadband``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Level:
    name: str
    at: float
    action: str = ""


@dataclass
class Hazard:
    key: str  # "smoke" | "heat" | "sensor"
    label: str
    unit: str
    levels: list[Level]
    deadband: float = 0.0
    reminder_hours: float = 4.0


@dataclass
class HazardState:
    level: int = 0
    last_notified: float = 0.0  # unix seconds

    def to_dict(self) -> dict:
        return {"level": self.level, "last_notified": self.last_notified}

    @classmethod
    def from_dict(cls, d: dict | None) -> "HazardState":
        d = d or {}
        return cls(int(d.get("level", 0)), float(d.get("last_notified", 0.0)))


@dataclass
class Decision:
    kind: str | None  # escalation | downgrade | all_clear | reminder | None
    new_state: HazardState
    level: Level | None = None
    extra: dict = field(default_factory=dict)


def level_for(value: float, hazard: Hazard, prev_level: int) -> int:
    raw = sum(1 for lv in hazard.levels if value >= lv.at)
    if raw >= prev_level:
        return raw
    lvl = prev_level
    while lvl > raw and value < hazard.levels[lvl - 1].at - hazard.deadband:
        lvl -= 1
    return lvl


def evaluate(value: float, hazard: Hazard, prev: HazardState, now: float) -> Decision:
    new_level = level_for(value, hazard, prev.level)
    current = hazard.levels[new_level - 1] if new_level else None

    if new_level > prev.level:
        kind = "escalation"
    elif new_level < prev.level:
        kind = "all_clear" if new_level == 0 else "downgrade"
    elif new_level > 0 and now - prev.last_notified >= hazard.reminder_hours * 3600:
        kind = "reminder"
    else:
        kind = None

    new_state = HazardState(new_level, now if kind else prev.last_notified)
    return Decision(kind, new_state, current)
