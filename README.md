# Hanwha Camera Analytics

**Install on a fresh Ubuntu server (one command):**

```bash
git clone https://github.com/JCEternal/MQTT-Dashboard.git && cd MQTT-Dashboard && bash install.sh
```

No `git` on the server? Use this instead (downloads the same files as a .tar.gz):

```bash
curl -fsSL https://github.com/JCEternal/MQTT-Dashboard/archive/refs/heads/main.tar.gz | tar xz && cd MQTT-Dashboard-main && bash install.sh
```

Records MQTT analytics from Hanwha Vision cameras (WiseAI object counting, loitering,
intrusion, etc.) and shows them on a Grafana dashboard. Everything runs in Docker.

```
Hanwha cameras ──MQTT──▶ cam-mosquitto  (built-in broker, port 1883)
                              │
                              ▼
                        cam-ingest      (records every message)
                              │
                              ▼
                        cam-db          (PostgreSQL)  ◀── cam-grafana  (http://<server>:3000)
```

## Requirements
- An Ubuntu Server 22.04/24.04 (or Debian 12) machine or VM: 2 CPU, 4 GB RAM, 32 GB disk
- Network access from the cameras to this machine on port **1883**
- Internet access during install (to download Docker and the container images)

## Install
1. On the server, run the one-line command at the top of this page. It downloads this
   repository into a folder called `MQTT-Dashboard` and starts the installer.
2. Answer the prompts (press **Enter** to accept defaults). The installer:
   - installs Docker if needed
   - asks for the broker, Grafana and database passwords (typed twice, hidden)
   - starts all containers
   - prints the **dashboard address** and the **settings to enter on each camera**

## Update to the latest version
Run this inside the `MQTT-Dashboard` folder. Your settings and data are kept
(`.env` is never stored in the repository, and the data lives in Docker volumes):
```bash
git pull && bash install.sh
```
Answer **Y** to "Keep existing settings".

## Moving from the old zip install (hanwha-mqtt-stack folder)
Both versions use the same container names (`cam-db`, `cam-grafana`...), so stop the
old one first or the installer stops with "container name is already in use":
```bash
cd ~/hanwha-mqtt-stack && docker compose down
```
The new folder starts with an empty database. To also delete the old data, use
`docker compose down -v` instead.

## Configure each camera
In the camera's web page: **Setup → Event → MQTT → Client Setup**
- MQTT: **Enable**
- Broker address / port / username / password: as printed by the installer
- Then enable WiseAI analytics publishing as usual.

Both WiseAI and non-WiseAI (built-in analytics) cameras are supported. Topics look like:

| Camera type | Topic |
|---|---|
| WiseAI | `[prefix]/onvif-ej/OpenApp/WiseAI/[Analytic]/&[source]/.../[rule name]` |
| Built-in analytics | `[prefix]/onvif-ej/[Analytic]/[Report type]/&[source]/[rule name]` (e.g. `PeopleCounting/PeopleCountPunctual`) |
| Classic video analytics (e.g. line crossing) | `[prefix]/onvif-ej/VideoAnalytics/tnssamsung:VideoAnalytics/&[source]/[line or area number]` (the payload says which analytic it is) |
| ONVIF rule engine (e.g. Queue Management) | `[prefix]/onvif-ej/RuleEngine/CountAggregation/QueueCountChanged/&[source]` (rule name comes from the payload) |

`[prefix]` is the camera's **Default Topic Prefix**: its MAC address unless you set a name.
Counting panels include any analytic that reports an IN/OUT count (WiseAI Object Counting,
People Counting, ...). Only per-event ("Punctual") reports are added up, so periodic summary
reports are never double-counted.

Line crossing: each `ObjectId` in a message is one object that crossed, so the count is the
number of ObjectIds. `Action` (Left/Right) is stored as the direction. Messages with
`State: false` are recorded but not counted.

Queue Management reports the **current queue length** each time it changes (a level, not
a count of crossings), so it has its own dashboard section (current length, length over
time as a step chart, peak per queue) and is never added into the IN/OUT totals.

## Day-to-day commands (run inside the folder)
| Task | Command |
|---|---|
| Is everything running? | `docker compose ps` |
| Watch messages arrive | `docker compose logs -f ingest` (Ctrl+C stops watching) |
| Broker log (connections, login failures) | `docker compose logs -f mosquitto` |
| Change settings | `nano .env` then `docker compose up -d` (or re-run `bash install.sh`) |
| Restart everything | `docker compose restart` |
| Stop everything | `docker compose down` (data is kept) |
| Update to newer images | `docker compose pull && docker compose up -d --build` |

## Settings (`.env`)
| Setting | Meaning |
|---|---|
| `COMPOSE_PROFILES` | `broker` = run the built-in broker. Empty = use an existing broker. |
| `MQTT_HOST` / `MQTT_PORT` | Where the recorder connects. `mosquitto` / `1883` for the built-in broker. |
| `BROKER_PUBLIC_PORT` | Port the cameras connect to (built-in broker). |
| `MQTT_USER` / `MQTT_PASS` | Broker login. Blank user = no login required. |
| `MQTT_TOPICS` | Topics to record. `+` = any one level, `#` = everything below. |
| `CAMERA_NAMES` | Friendly names: `prefix=Name,prefix=Name` (prefix = MAC or topic prefix) |
| `GRAFANA_*` | Dashboard port and admin login |

## Troubleshooting: no data on the dashboard
1. `docker compose ps`: all containers should be **Up**.
2. `docker compose logs mosquitto`: look for camera connections or `not authorised` (wrong camera login).
3. `docker compose logs ingest --tail 50`: should show `Connected`, `Subscribed`, then topic lines.
4. Test from any machine: `mosquitto_sub -h <server-ip> -u <user> -P <pass> -t '+/onvif-ej/#' -v`
5. Check the data: `docker exec cam-db psql -U camdata -d camdata -c "SELECT (SELECT count(*) FROM bi_events) AS bi, (SELECT count(*) FROM analytics_events) AS analytics;"`
6. In Grafana, widen the time range and set the dropdowns to **All**.

If port 1883 is already in use (another broker on this machine), re-run `bash install.sh`
and either choose a different port or point the stack at the existing broker.

## Dashboards
Business Intelligence and Analytics are kept completely separate: separate database
tables, separate dashboards. Links at the top of each dashboard switch between them.

| Dashboard | Table | Contents |
|---|---|---|
| **Hanwha – Business Intelligence** (home page) | `bi_events` | People / Vehicle / WiseAI object counting (IN, OUT, net), queue management, crowd count |
| **Hanwha – Analytics** | `analytics_events` | Line crossing (LEFT/RIGHT per line), area events (loitering, intrusion, ...), slip and fall (triggers per camera and rule) |
| **Hanwha – Raw MQTT messages** | `mqtt_messages` | Every message exactly as received, for troubleshooting |

Which table a message goes to is decided by its analytic name: names containing
count, queue, heatmap, occupancy or dwell are Business Intelligence; everything else is
Analytics (`BI_KEYWORDS` in `ingest/ingest.py`).

Classic video analytics: line rules report `Action` = Left/Right (stored as direction);
area rules report `Action` = the analytic (Loitering, Intrusion, ...). Each `ObjectId` is
one object, and `State: false` (event ended) is recorded but not counted.

New analytic types are recorded automatically. To chart a field without its own column:
```sql
SELECT $__timeGroupAlias(event_time, $__interval), avg((data->>'Level')::float) AS level
FROM analytics_events
WHERE analytic = 'SomeAnalytic' AND $__timeFilter(event_time)
GROUP BY 1 ORDER BY 1;
```

## Housekeeping
- Data lives in Docker volumes and survives restarts, reboots and upgrades.
- Delete data older than 180 days (optional):
  `docker exec cam-db psql -U camdata -d camdata -c "DELETE FROM mqtt_messages WHERE received_at < now() - interval '180 days'; DELETE FROM analytics_events WHERE event_time < now() - interval '180 days'; DELETE FROM bi_events WHERE event_time < now() - interval '180 days';"`
- Back up the database:
  `docker exec cam-db pg_dump -U camdata camdata | gzip > camdata-$(date +%F).sql.gz`
- Remove everything **including all data**: `docker compose down -v`

## For developers
- The Grafana dashboards are **generated**: edit `tools/gen_dashboards.py`, then run
  `python3 tools/gen_dashboards.py grafana/dashboards` and restart Grafana
  (`docker compose restart grafana`). Don't hand-edit the JSON files.
- Message parsing and BI/Analytics routing live in `ingest/ingest.py`
  (`parse_topic()`, `record_analytic()`, `BI_KEYWORDS`).
