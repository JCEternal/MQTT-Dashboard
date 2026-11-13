# Hanwha Camera Analytics: Project Context

_Last updated: 2026-11-13 (moved to GitHub)._ This is the reference document for the project: what it is,
how it works, the rules we settled on, and what is still open._

---

## 1. Goal

Record MQTT data from Hanwha Vision cameras (WiseAI and non-WiseAI models) and show it
on Grafana dashboards. Everything runs in Docker on an Ubuntu VM (Proxmox host). It's
packaged so anyone on the team can deploy it with one command: `bash install.sh`.

The code lives in the GitHub repository **JCEternal/MQTT-Dashboard** (public). Install on a server with
`git clone https://github.com/JCEternal/MQTT-Dashboard.git && cd MQTT-Dashboard && bash install.sh`;
update with `git pull && bash install.sh`. (Earlier versions were shipped as hanwha-camera-analytics.zip.)

---

## 2. Architecture

```
Hanwha cameras ──MQTT──▶ cam-mosquitto (built-in broker, optional, port 1883)
                               │
                               ▼
                         cam-ingest   (Python: parses + routes every message)
                               │
               ┌───────────────┼────────────────┐
               ▼               ▼                ▼
        mqtt_messages      bi_events      analytics_events      (PostgreSQL: cam-db)
         (raw archive)   (Business Intel.)    (Analytics)
               └───────────────┴────────────────┘
                               ▲
                         cam-grafana  (3 dashboards, port 3000)
```

| Container | Image | Purpose |
|---|---|---|
| cam-mosquitto | eclipse-mosquitto:2 | Built-in broker. Only runs when `COMPOSE_PROFILES=broker`. Config + password file generated from `.env` at every start (`mosquitto/start-mosquitto.sh`). |
| cam-ingest | built from `ingest/` (python:3.12-slim, paho-mqtt 2, psycopg 3) | Subscribes to `MQTT_TOPICS`, writes raw + structured rows. Creates/migrates tables itself. |
| cam-db | postgres:16 | Data in Docker volume `db-data`. Not exposed to the LAN. |
| cam-grafana | grafana/grafana:latest | Data source + dashboards auto-provisioned. Home page = BI dashboard. |

### Files
| Path | What |
|---|---|
| `install.sh` | Interactive installer: installs Docker if needed, asks questions (incl. passwords, hidden + confirmed), writes `.env`, starts stack, applies passwords to existing DB/Grafana, prints camera settings. Re-runnable. |
| `docker-compose.yml` | 4 services; all settings come from `.env`. |
| `.env.example` | Template. The real `.env` is created by the installer (chmod 600). |
| `ingest/ingest.py` | Topic parsing, payload parsing, BI/Analytics routing, DB schema + upgrade migration. |
| `db/init/01_schema.sql` | Tables for a fresh database. |
| `mosquitto/start-mosquitto.sh` | Builds the broker config from env vars at every start. |
| `grafana/provisioning/...` | Data source (`CameraDB`, uid `camdb`) and dashboard provider. |
| `grafana/dashboards/*.json` | **Generated**. Do not hand-edit. |
| `tools/gen_dashboards.py` | Generates the 3 dashboard JSON files: `python3 tools/gen_dashboards.py grafana/dashboards` |

### Key `.env` settings
`COMPOSE_PROFILES` (`broker` or empty), `MQTT_HOST` (`mosquitto` for the built-in broker), `MQTT_PORT`,
`BROKER_PUBLIC_PORT`, `MQTT_USER`, `MQTT_PASS`, `MQTT_TOPICS` (default `+/onvif-ej/#`),
`CAMERA_NAMES` (`prefix=Name,prefix=Name`), `POSTGRES_*`, `GRAFANA_PORT`, `GRAFANA_ADMIN_*`, `TZ` (IANA zone name;
install.sh offers a numbered North American menu plus "Other", defaulting to the previous .env value or the VM's zone).

---

## 3. Hanwha MQTT message formats (real samples)

Topic shape: `[prefix]/onvif-ej/[analytic path]/&[video source]/.../[rule name]`
- `[prefix]` = the camera's **Default Topic Prefix**: its MAC address by default, or a name
  (e.g. `TestCam`). Treated as a MAC only if it looks like one; otherwise used as the camera name.
- Payload is JSON: `UtcTime`, `Source` (video source, rule), `Data` (the values).

| # | Type | Category | Topic (after prefix) | Payload `Data` |
|---|---|---|---|---|
| 1 | WiseAI Object Counting | BI | `onvif-ej/OpenApp/WiseAI/ObjectCounting/&VideoSourceToken-0/2/name1` | `ReportType: Punctual, ObjectType: Vehicle, Direction: IN, Count: "1"` (Source has `CountingRuleIndex`, `RuleName`) |
| 2 | People Counting (built-in) | BI | `onvif-ej/PeopleCounting/PeopleCountPunctual/&VideoEncoderToken-01-0/TestLine1` | `Direction: "in", Count: "3"` (Source: `VideoSourceConfigToken`, `RuleName`) |
| 3 | Vehicle Counting (built-in) | BI | `onvif-ej/VehicleCounting/VehicleCountPunctual/&VideoEncoderToken-01-0/vehicleline1` | `Direction: "in", Count: "1"` |
| 4 | Queue Management | BI | `onvif-ej/RuleEngine/CountAggregation/QueueCountChanged/&VideoSourceToken-0` (no rule in topic) | `Count: "1"` = **current queue length**. RuleName in Source. |
| 5 | WiseAI Crowd Count | BI | `onvif-ej/OpenApp/WiseAI/CrowdCountChanged/&vs-0/crowdcount1` | `Count: "12"` = **current crowd size**, sent on change |
| 6 | Line Crossing (classic IVA) | Analytics | `onvif-ej/VideoAnalytics/tnssamsung:VideoAnalytics/&VideoSourceToken-0/1` | `State: "true", ObjectId: "4162", Action: "Left"`. Source: `Line: "1"` |
| 7 | Loitering / Intrusion (classic IVA area) | Analytics | same topic as #6 | `State: "true", ObjectId: "5366", Action: "Loitering"`. Source: `Area: "1"` |
| 8 | WiseAI Slip and Fall | Analytics | `onvif-ej/OpenApp/WiseAI/SlipAndFallDetection/&vs-0/slipandfall` | `State: "true", ObjectId: "30911"` |

Exact sample payloads:
```json
{"UtcTime":"2026-10-17T23:40:00Z","Source":{"VideoSourceToken":"VideoSourceToken-0","CountingRuleIndex":"2","RuleName":"name1"},"Data":{"ReportType":"Punctual","ObjectType":"Vehicle","Direction":"IN","Count":"1"}}
{"UtcTime":"2026-10-17T23:45:43.636Z","Source":{"VideoSourceConfigToken":"VideoEncoderToken-01-0","RuleName":"TestLine1"},"Data":{"Direction":"in","Count":"3"}}
{"UtcTime":"2026-10-17T23:54:13.471Z","Source":{"VideoSourceConfigToken":"VideoEncoderToken-01-0","RuleName":"vehicleline1"},"Data":{"Direction":"in","Count":"1"}}
{"UtcTime":"2026-10-17T23:48:11.458Z","Source":{"VideoSourceToken":"VideoSourceToken-0","RuleName":"TestQueue1"},"Data":{"Count":"1"}}
{"UtcTime":"2026-10-18T15:41:05.635Z","Source":{"VideoSourceToken":"vs-0","RuleName":"crowdcount1"},"Data":{"Count":"12"}}
{"UtcTime":"2026-10-17T23:49:14.173Z","Source":{"VideoSourceToken":"VideoSourceToken-0","Line":"1"},"Data":{"State":"true","ObjectId":"4162","Action":"Left"}}
{"UtcTime":"2026-10-17T23:58:08.701Z","Source":{"VideoSourceToken":"VideoSourceToken-0","Area":"1"},"Data":{"State":"true","ObjectId":"5366","Action":"Loitering"}}
{"UtcTime":"2026-10-19T18:15:13.628Z","Source":{"VideoSourceToken":"vs-0","RuleName":"slipandfall"},"Data":{"State":"true","ObjectId":"30911"}}
```

---

## 4. How messages are parsed (`ingest/ingest.py`)

**Topic → app / analytic** (`parse_topic`)
| Topic path starts with | app | analytic |
|---|---|---|
| `OpenApp/WiseAI/X/...` | WiseAI | X (e.g. ObjectCounting, CrowdCountChanged, SlipAndFallDetection) |
| `RuleEngine/A/B` | RuleEngine | `A/B` (e.g. CountAggregation/QueueCountChanged) |
| `VideoAnalytics/...` | VideoAnalytics | decided from payload: `Line` → **LineCrossing**; `Area` → the `Action` value (Loitering, Intrusion...) |
| `X/Y` (anything else) | Built-in | X (e.g. PeopleCounting), report_type = Y (e.g. PeopleCountPunctual) |

Rule name: `Source.RuleName`, else "Line N" / "Area N", else the last topic level.
Video source: `VideoSourceToken` / `VideoSourceConfigToken`, else the `&...` topic level.

**Payload → columns** (`record_analytic`)
- `direction` = `Direction`, uppercased (`in` → `IN`). For line crossing, `Action` (Left/Right) is the direction.
- `object_type` = `ObjectType`; defaults to **Person** for People Counting, **Vehicle** for Vehicle Counting.
- `count` = `Count`; if absent, the number of distinct `ObjectId`s (only when `State` isn't `false`).
- `state`, `report_type`, plus the full `source` and `data` JSON are always stored.
- Event time = camera's `UtcTime` (falls back to arrival time).

**Routing: BI vs Analytics are never mixed.**
`BI_KEYWORDS = ("count", "queue", "heatmap", "occupancy", "dwell")`. An analytic whose name contains
one of these goes to `bi_events`, everything else to `analytics_events`. Every message also goes to `mqtt_messages`.
On startup, ingest migrates BI rows out of `analytics_events` (an older version mixed them).

---

## 5. Rules and decisions we settled on

1. **Business Intelligence and Analytics are separate items** (Hanwha terminology): separate
   tables, separate dashboards, data never mixed.
   - BI = people/vehicle/object counting, queue management, crowd count (heatmap etc. later)
   - Analytics = line crossing, loitering, intrusion, slip and fall
2. **Counting totals add up only "Punctual" reports** (`report_type` null or containing "punctual"),
   so periodic summary reports can never double-count.
3. **Queue length and crowd count are levels, not tallies.** They are never summed. They're shown as
   current / peak / step charts.
4. **Line crossing reports Left/Right, not In/Out.** It's shown as LEFT/RIGHT and not added to IN/OUT totals.
5. **`State: false` (event ended) is recorded but not counted**, so one event = one count.
6. **Repeated JSON keys seen in early samples were MQTT Explorer's "compare with previous message"
   diff view, not the camera.** Real messages have one value per key. The parser still keeps all values of
   a repeated key as a list (columns use the last one) as a safety net.
7. Passwords are **asked for** by the installer (hidden, typed twice, min 8 chars, no spaces or `$ # ' " \ ``).
   The installer re-applies them to Postgres (`ALTER USER`) and Grafana (`grafana cli admin reset-admin-password`)
   on every run, because both only read their password on first start.
8. Topic filter default `+/onvif-ej/#` (all cameras, WiseAI and non-WiseAI).

---

## 6. Dashboards (generated by `tools/gen_dashboards.py`)

Each has Camera / Type / Rule filters (multi-select, "All" = `'__all'`) and links to the others.

**Hanwha – Business Intelligence** (`hanwha-bi`, table `bi_events`, home page)
- Overview: events, cameras reporting, last event
- Counting: Total IN, Total OUT, Net; counts over time by object type & direction; totals table
- Queue management: current length (bar gauge, yellow 5 / red 10), length over time (step), summary
- Crowd count: current (bar gauge, yellow 20 / red 50), over time (step), summary with peak + peak time
- Latest BI events

**Hanwha – Analytics** (`hanwha-analytics`, table `analytics_events`)
- Overview: events, cameras, last event, events over time by type, events by camera/type/rule
- Line crossing: objects crossed, LEFT, RIGHT, crossings over time, totals by line
- Area events (loitering, intrusion; excludes slip and fall): counts, over time, by area
- Slip and fall: triggers (red at ≥1), cameras with triggers, last trigger, over time, by camera & rule
- Latest analytics events

**Hanwha – Raw MQTT messages** (`hanwha-raw`, table `mqtt_messages`)

---

## 7. Status

- **2026-10-29: the full Docker stack was run end to end** with the real `install.sh` (scripted answers):
  all 4 containers Up, the 8 real samples published through Mosquitto with login, rows correct in
  `mqtt_messages` / `bi_events` / `analytics_events`, every dashboard and variable query run through
  Grafana's own API (0 errors), dashboards checked in a browser. Also passed: publishing through the
  published port 1883 from outside the containers, wrong broker password rejected, restart keeps data,
  re-running the installer with new passwords (Postgres, Grafana, broker all switch over).
- **Bug found and fixed in `tools/gen_dashboards.py`:** the five "Last ..." stat panels showed
  "No data" (a stat panel ignores time-type fields by default) and would have been red (default
  thresholds). Fixed with a shared `LAST_TIME` setting (`reduceOptions.fields = /^Last$/`, fixed green colour).
- Normal on re-run with a new DB password: ingest logs one "password authentication failed" warning,
  then connects 5 s later once the installer has run `ALTER USER`.
- An earlier version deployed on the VM showed no data in Grafana. Redeploy the latest bundle on the VM,
  then use README "Troubleshooting". Note dashboards default to the last 24 hours, so old test data is hidden.

## 8. Open items / ideas

1. **SSL/TLS (planned, not built):** a private CA created by the installer, server cert with the VM's IP + hostname in
   SAN, Mosquitto TLS listener on 8883 (optionally keep 1883 during migration), Grafana HTTPS. Cameras need
   NTP. **Waiting on:** which TLS options the cameras' MQTT Client Setup page offers (CA upload vs. a
   "verify" toggle). Optional later: client certificates (mutual TLS).
2. Optional per-line mapping of line-crossing Left/Right → IN/OUT.
3. Time-weighted average for queue length / crowd count (a plain average is misleading).
4. Data retention schedule (delete rows older than N days).
5. Distribution: done via the public GitHub repo (2026-11-13). A Proxmox VM template is still an option.
6. Pin the Grafana image version instead of `latest` for reproducible deploys.
7. Changing the Grafana admin *username* on a re-run doesn't rename the account (only the password updates).

## 9. Adding a new analytic type (checklist)

1. Get a real topic + payload (copy from MQTT Explorer's value panel or `mosquitto_sub -v`, **not** the diff view).
2. Check how `parse_topic()` reads the topic and what `record_analytic()` produces.
3. Decide BI vs Analytics. Update `BI_KEYWORDS` (and the SQL pattern in the migration) if the name doesn't route correctly.
4. Decide whether the number is a **tally** (sum it), a **level** (current/peak), or a **trigger** (count events).
5. Add panels in `tools/gen_dashboards.py`, then regenerate.
6. Test: publish the sample to a broker, check the table rows, and run every dashboard query.
