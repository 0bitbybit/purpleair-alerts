<p align="center">
  <img src="docs/summary.png" alt="PurpleAir heat and smoke alerts project summary" width="100%">
</p>
# PurpleAir Heat + Wildfire Smoke Alerts

[![Tests](https://github.com/0bitbybit/purpleair-alerts/actions/workflows/tests.yml/badge.svg)](https://github.com/0bitbybit/purpleair-alerts/actions/workflows/tests.yml)

Email and text-message alerts for outdoor heat and wildfire smoke, built from
low-cost PurpleAir sensors, a few hundred lines of Python, and free open-source tooling.

This is an open-source rebuild of a workplace monitoring system I originally
built in Microsoft Power Automate with alerts posted to Teams. The goal here is
the same safety logic with no licensed platform behind it: anyone with a
PurpleAir sensor and a GitHub account can run it.

## What it does

Every 15 minutes it:

1. **Reads** each PurpleAir sensor through the PurpleAir API.
2. **Corrects** the data the way a regulator would:
   - PM2.5 uses the **EPA US-wide correction** for PurpleAir (Barkjohn et al., 2021),
     including the 2023 high-concentration extension used on the AirNow Fire and
     Smoke Map. Raw PurpleAir numbers run high in smoke; uncorrected, they'd over-alert.
   - AQI uses the **February 2024 EPA PM2.5 breakpoints**.
   - Temperature/humidity get PurpleAir's published housing offsets (−8 °F, +4 % RH),
     and the NWS **heat index** is computed.
   - The sensor's two laser counters (A/B) are cross-checked with the EPA
     agreement rule; if they disagree, smoke alerts are held and a
     "check the sensor" notice goes out instead.
3. **Evaluates** each hazard against configurable action levels (defaults modeled
   on Cal/OSHA §3395 heat illness and §5141.1 wildfire smoke triggers).
4. **Notifies** by email and SMS — only when something changes:

| Event | When it fires |
|---|---|
| **ALERT** | A higher action level is reached |
| **UPDATE** | Conditions improve but are still above a level |
| **ALL CLEAR** | Back below every level |
| **REMINDER** | Still elevated after `reminder_hours` (default 4 h) |

A **deadband** (hysteresis) stops a reading hovering at 150–152 AQI from sending
an alert every 15 minutes.

### Example alert (email)

```
Subject: [Example Site] ALERT: Loading dock (outdoor): Smoke (PM2.5 AQI) — Wildfire smoke rule trigger (168 AQI)

PM2.5 AQI 168 (Unhealthy), PM2.5 80.4 µg/m³ (EPA-corrected)
Temp 96 °F, RH 22%, heat index 93 °F

Action: Cal/OSHA §5141.1: communicate, reduce exposure, provide N95s for voluntary use.

As of Sat Oct 17, 12:53 PM PDT
https://map.purpleair.com/?select=123456
```

The SMS version is the same headline and action in plain characters, kept short.

## Architecture

```mermaid
flowchart LR
    A[GitHub Actions<br/>cron every 15 min] --> B[PurpleAir API]
    B --> C[Correct + compute<br/>EPA PM2.5, AQI, heat index]
    C --> D{Rules engine<br/>levels + deadband}
    S[(state.json<br/>Actions cache)] <--> D
    D -- changed --> E[Apprise]
    E --> F[Email]
    E --> G[SMS]
```

### Power Automate → open source

| Power Automate (original) | This repo |
|---|---|
| Recurrence trigger | GitHub Actions `schedule` (cron) |
| HTTP action to PurpleAir | `aqalert/purpleair.py` (`requests`) |
| Condition / Switch steps | `aqalert/rules.py` — levels, deadband, reminders |
| Variables between runs | `state.json` persisted in the Actions cache |
| Post message to Teams | [Apprise](https://github.com/caronc/apprise) → email + SMS |
| Flow run history | Actions run logs; a failed run emails the repo owner |
| Connections / credentials | GitHub encrypted secrets |

Apprise speaks 100+ services through one URL format, so adding Slack, Discord,
ntfy push, or back to Teams is a secret change, not a code change.

## Quick start

**Try it with no API key or accounts:**

```bash
pip install -r requirements.txt
python -m aqalert --demo tests/fixtures/smoky_hot_day.json --dry-run
```

**Run it for real on GitHub:**

1. Fork or clone this repo.
2. Copy `config.example.yaml` to `config.yaml` and set your sensor indices,
   labels, and action levels. *(Sensor indices are visible on the PurpleAir map.
   If you'd rather not publish them, put the whole config in a `CONFIG_YAML`
   secret instead of committing it.)*
3. Add repository secrets (**Settings → Secrets and variables → Actions**):

   | Secret | Example |
   |---|---|
   | `PURPLEAIR_API_KEY` | Read key from develop.purpleair.com |
   | `EMAIL_URLS` | `mailtos://you:app-password@gmail.com?to=you@example.com` |
   | *or, for phone push via ntfy* | `ntfy://your-long-random-topic?priority=high` |
   | `SMS_URLS` | `twilio://ACCOUNT_SID:AUTH_TOKEN@+15551230000/+15559870000` |

   Multiple recipients: separate URLs with spaces or commas.
4. Open **Actions → Check air quality and heat → Run workflow**, tick
   **Only send a test email/SMS**, and run it to confirm delivery. Run it again
   unticked for a real check.

**Run it locally or on a Raspberry Pi** instead: fill in `.env` from
`.env.example`, then schedule `python -m aqalert` with cron or a systemd timer.

## Notes on text messages

- **Twilio** (or Vonage, Plivo, AWS SNS — all supported by Apprise) is the
  reliable route. US numbers must be registered (A2P 10DLC or toll-free
  verification) before carriers will deliver texts.
- **Carrier email-to-text gateways** (`number@carrier-gateway`) cost nothing and
  work as an `EMAIL_URLS` entry, but carriers have been shutting them down or
  filtering them, so don't rely on one for a safety alert.
- **ntfy** (`ntfy://your-topic`) is a free, open-source push alternative to SMS:
  no account, no carrier registration. Install the ntfy app, subscribe to a long
  random topic name (anyone who knows it can read it), and put the URL in `EMAIL_URLS`.

## Limitations — read before relying on it

- This is a **screening and early-warning tool**, not a compliance measurement.
  PurpleAir temperature is not a shade dry-bulb or WBGT reading; a sensor in
  direct sun will read high. PM2.5 is an estimate even after correction, and
  §5141.1 is written around the *current AQI*, typically taken from AirNow or
  the local air district. Confirm conditions before acting on a reading alone.
- Alerts are based on near-real-time readings, while the official AQI uses
  longer averages, so this will react faster than the AirNow NowCast.
- GitHub may start scheduled workflows several minutes late, and disables
  scheduled workflows in repos with no activity for 60 days.
- The action text in `config.example.yaml` is an example. Match it to your own
  written heat illness and wildfire smoke programs.

## Tests

```bash
pip install pytest && pytest -q
```

Covers the AQI breakpoints, correction continuity, heat index against the NWS
table, the rules engine (escalation, deadband, reminder, all-clear), and an
end-to-end demo run.

## Project layout

```
aqalert/
  purpleair.py   API client + demo loader
  aqi.py         EPA PM2.5 correction, AQI, A/B channel check
  heat.py        NWS heat index
  rules.py       action levels, deadband, reminders
  notify.py      Apprise email + SMS
  main.py        fetch → correct → evaluate → notify → save state
config.example.yaml
.github/workflows/check-air.yml   the 15-minute schedule
.github/workflows/tests.yml       CI
```

## References

- Barkjohn, K. K., Gantt, B., & Clements, A. L. (2021). Development and application
  of a United States-wide correction for PM2.5 data collected with the PurpleAir
  sensor. *Atmospheric Measurement Techniques*, 14, 4617–4637.
- U.S. EPA, *Technical Assistance Document for the Reporting of Daily Air Quality*
  (AQI), 2024 PM2.5 revision.
- NWS heat index equation (Rothfusz regression with adjustments).
- Cal/OSHA Title 8 §3395 (Heat Illness Prevention), §5141.1 (Protection from
  Wildfire Smoke).

## License

MIT
