"""
MQTT -> PostgreSQL recorder.

Subscribes to the topics in MQTT_TOPICS on your Mosquitto broker and:
  1. writes EVERY message into mqtt_messages (raw archive, nothing lost)
  2. writes a clean, structured row into exactly ONE of two separate tables:
       bi_events        - Business Intelligence: people / vehicle / object
                          counting, queue management, heatmap...
       analytics_events - Analytics: line crossing, loitering, intrusion...
     The two are never mixed. Common fields (rule, object type, direction,
     count, state) get columns; the full Source/Data blocks are kept as JSON.
"""
import json
import logging
import os
import re
import time
from datetime import datetime

import paho.mqtt.client as mqtt
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER") or None
MQTT_PASS = os.environ.get("MQTT_PASS") or None
TOPICS = [t.strip() for t in os.environ.get("MQTT_TOPICS", "#").split(",") if t.strip()]

# "E4:30:22:C5:6F:14=Lobby,AA:BB:..=Dock" -> {"E4:30:22:C5:6F:14": "Lobby", ...}
CAMERA_NAMES = {}
for pair in os.environ.get("CAMERA_NAMES", "").split(","):
    if "=" in pair:
        mac, name = pair.split("=", 1)
        CAMERA_NAMES[mac.strip().upper()] = name.strip()

# Hanwha's terminology: counting / queue / heatmap = Business Intelligence,
# detection events (line crossing, loitering, intrusion...) = Analytics.
# Keep this list in step with BI_PATTERN in the SQL below.
BI_KEYWORDS = ("count", "queue", "heatmap", "occupancy", "dwell")


def is_business_intelligence(analytic):
    a = (analytic or "").lower()
    return any(k in a for k in BI_KEYWORDS)


EVENT_COLUMNS = """
    id           BIGSERIAL PRIMARY KEY,
    event_time   TIMESTAMPTZ NOT NULL,   -- camera's UtcTime (falls back to arrival time)
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    camera       TEXT,                   -- friendly name (or prefix / MAC)
    mac          TEXT,
    app          TEXT,                   -- WiseAI, Built-in, RuleEngine, VideoAnalytics
    analytic     TEXT,                   -- e.g. PeopleCounting, LineCrossing
    video_source TEXT,
    rule_index   INT,
    rule_name    TEXT,                   -- rule / line / queue name
    report_type  TEXT,                   -- e.g. Punctual
    object_type  TEXT,                   -- e.g. Person, Vehicle
    direction    TEXT,                   -- IN / OUT, or LEFT / RIGHT for line crossing
    count        INT,
    state        TEXT,
    source       JSONB,                  -- full Source block
    data         JSONB                   -- full Data block
"""
COPY_COLUMNS = ("event_time, received_at, camera, mac, app, analytic, video_source, "
                "rule_index, rule_name, report_type, object_type, direction, count, "
                "state, source, data")

# Tables are also created here (not only in db/init) so that upgrading an
# already-running stack picks up changes without wiping the database.
SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS mqtt_messages (
    id           BIGSERIAL PRIMARY KEY,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic        TEXT NOT NULL,
    camera       TEXT,
    event        TEXT,
    payload      TEXT,
    payload_json JSONB
);
CREATE TABLE IF NOT EXISTS bi_events        ({EVENT_COLUMNS});
CREATE TABLE IF NOT EXISTS analytics_events ({EVENT_COLUMNS});
CREATE INDEX IF NOT EXISTS idx_msgs_time ON mqtt_messages (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_bi_time   ON bi_events (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_bi_lookup ON bi_events (analytic, camera, rule_name, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_an_time   ON analytics_events (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_an_lookup ON analytics_events (analytic, camera, rule_name, event_time DESC);

-- Upgrade from older versions, which kept everything in analytics_events:
-- move Business Intelligence rows into bi_events (does nothing once done).
WITH moved AS (
    DELETE FROM analytics_events
    WHERE analytic ~* '(count|queue|heatmap|occupancy|dwell)'
    RETURNING {COPY_COLUMNS}
)
INSERT INTO bi_events ({COPY_COLUMNS}) SELECT {COPY_COLUMNS} FROM moved;
"""

INSERT_RAW = """
    INSERT INTO mqtt_messages (topic, camera, event, payload, payload_json)
    VALUES (%s, %s, %s, %s, %s)
"""
INSERT_EVENT = """
    INSERT INTO {table} (event_time, camera, mac, app, analytic, video_source,
        rule_index, rule_name, report_type, object_type, direction, count, state,
        source, data)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
INSERT_BI = INSERT_EVENT.format(table="bi_events")
INSERT_ANALYTIC = INSERT_EVENT.format(table="analytics_events")


def connect_db():
    while True:
        try:
            conn = psycopg.connect(autocommit=True)  # uses PGHOST/PGUSER/... env vars
            conn.execute(SCHEMA_SQL)
            log.info("Connected to PostgreSQL")
            return conn
        except Exception as exc:
            log.warning("DB not ready (%s); retrying in 5s", exc)
            time.sleep(5)


db = connect_db()


def run(sql, params):
    """Execute, reconnecting once if the DB connection dropped."""
    global db
    try:
        db.execute(sql, params)
    except psycopg.OperationalError:
        log.warning("Lost DB connection; reconnecting")
        db = connect_db()
        db.execute(sql, params)


MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$")


def parse_topic(topic: str):
    """Returns a dict with mac, camera, event, app, analytic, and topic fallbacks
    for video_source / rule_index / rule / report_type (used when the payload lacks them).

    Hanwha topics: [prefix]/onvif-ej/[analytic path]/&[source]/.../[rule name]
    The prefix is the camera's "Default Topic Prefix": its MAC by default,
    or any name you set (e.g. TestCam).

    WiseAI camera:
      'E4:30:22:C5:6F:14/onvif-ej/OpenApp/WiseAI/ObjectCounting/&VideoSourceToken-0/2/name1'
        -> app 'WiseAI', analytic 'ObjectCounting', rule_index 2, rule 'name1'
    Built-in (non-WiseAI) analytics:
      'TestCam/onvif-ej/PeopleCounting/PeopleCountPunctual/&VideoEncoderToken-01-0/TestLine1'
        -> camera 'TestCam', app 'Built-in', analytic 'PeopleCounting',
           report_type 'PeopleCountPunctual', rule 'TestLine1'
    Standard ONVIF rule engine (e.g. Queue Management):
      'TestCam/onvif-ej/RuleEngine/CountAggregation/QueueCountChanged/&VideoSourceToken-0'
        -> app 'RuleEngine', analytic 'CountAggregation/QueueCountChanged'
           (rule name comes from the payload's RuleName)
    Classic video analytics (IVA):
      'TestCam/onvif-ej/VideoAnalytics/tnssamsung:VideoAnalytics/&VideoSourceToken-0/1'
        -> app 'VideoAnalytics'; the payload decides the analytic (e.g. LineCrossing)
    Other:  'cameras/lobby-01/motion' -> camera 'lobby-01', analytic 'motion'
    """
    out = dict(mac=None, camera=None, event=None, app=None, analytic=None,
               rule=None, rule_index=None, video_source=None, report_type=None)
    parts = [p for p in topic.split("/") if p]
    if not parts:
        return out
    if len(parts) >= 2 and parts[1].lower().startswith("onvif"):
        prefix = parts[0]
        mac = prefix.upper() if MAC_RE.match(prefix) else None
        head, tail = [], []
        for p in parts[2:]:
            (tail if (tail or p.startswith("&")) else head).append(p)
        if head and head[0] == "OpenApp" and len(head) >= 3:
            app, analytic, sub = head[1], head[2], head[3:]   # OpenApp/WiseAI/ObjectCounting
        elif head and head[0] == "RuleEngine" and len(head) >= 2:
            # standard ONVIF rule events, e.g. RuleEngine/CountAggregation/QueueCountChanged
            app, analytic, sub = "RuleEngine", "/".join(head[1:]), []
        elif head and head[0] == "VideoAnalytics":
            # classic IVA events (line crossing, area detection...). The topic doesn't
            # say which; record_analytic() works it out from the payload.
            app, analytic, sub = "VideoAnalytics", "VideoAnalytics", []
        elif head:
            app, analytic, sub = "Built-in", head[0], head[1:]  # PeopleCounting/PeopleCountPunctual
        else:
            app, analytic, sub = None, parts[1], []
        out.update(mac=mac,
                   camera=CAMERA_NAMES.get(prefix.upper(), prefix),
                   event="/".join(head) or parts[1],
                   app=app, analytic=analytic,
                   report_type=sub[-1] if sub else None,
                   rule=tail[-1] if len(tail) >= 2 else None,
                   video_source=tail[0].lstrip("&") if tail else None,
                   # a purely numeric level between the source and the name = rule index
                   rule_index=next((to_int(p) for p in tail[1:-1] if p.isdigit()), None))
        return out
    out.update(camera=parts[1] if len(parts) >= 3 else parts[0],
               event=parts[-1], analytic=parts[-1])
    return out


class DuplicateKeyTracker:
    """JSON decoder hook for repeated keys.

    Hanwha can legitimately repeat a key, e.g. one "ObjectId" per object that
    crossed a line. Standard JSON parsing would silently keep only the last one,
    so instead ALL values are kept as a list (["4162", "4156"]). The full list is
    stored in the database; single-value columns use the last value."""
    def __init__(self):
        self.duplicates = set()

    def __call__(self, pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                self.duplicates.add(k)
                out[k] = (out[k] if isinstance(out[k], list) else [out[k]]) + [v]
            else:
                out[k] = v
        return out


def last(value):
    """Repeated key -> list of values; for single-value columns use the last one."""
    return value[-1] if isinstance(value, list) and value else value


def as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def to_int(value, default=None):
    try:
        return int(last(value))
    except (TypeError, ValueError):
        return default


def parse_time(value):
    try:
        return datetime.fromisoformat(str(last(value)).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().astimezone()


def first(d, *keys):
    """First key that exists in dict d (Hanwha uses slightly different names per analytic)."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def flat(value):
    value = last(value)
    return None if value is None else str(value)


def record_analytic(t, payload):
    """Write a structured row for any ONVIF-style message with a 'Data' block,
    into bi_events (Business Intelligence) or analytics_events (Analytics)."""
    if not isinstance(payload, dict):
        return
    data = payload.get("Data")
    if not isinstance(data, dict):
        return
    src = payload.get("Source") if isinstance(payload.get("Source"), dict) else {}

    analytic = t["analytic"]
    line = first(src, "Line", "LineIndex")
    area = first(src, "Area", "AreaIndex", "Region", "Field")
    action = flat(data.get("Action"))
    if t["app"] == "VideoAnalytics":              # classic IVA: payload says what it is
        if line is not None:
            analytic = "LineCrossing"             # Action = Left / Right (the direction)
        elif area is not None:
            # On area rules, Action IS the analytic: Loitering, Intrusion, Entering...
            analytic = action or "AreaDetection"

    rule_index = to_int(first(src, "CountingRuleIndex", "RuleIndex", "Index"),
                        to_int(line, to_int(area, t["rule_index"])))
    rule_name = flat(first(src, "RuleName", "Rule", "Name"))
    if not rule_name and line is not None:
        rule_name = f"Line {flat(line)}"
    elif not rule_name and area is not None:
        rule_name = f"Area {flat(area)}"
    rule_name = rule_name or t["rule"]

    state = flat(first(data, "State", "IsActive", "Status"))
    direction = flat(data.get("Direction"))
    if not direction and line is not None:
        direction = action                       # line crossing: Action = Left/Right
    if direction:
        direction = direction.upper()            # "in" -> "IN", "Left" -> "LEFT"
    object_type = flat(first(data, "ObjectType", "Type", "Class"))
    if not object_type and "people" in (analytic or "").lower():
        object_type = "Person"                   # People Counting only counts people
    elif not object_type and "vehicle" in (analytic or "").lower():
        object_type = "Vehicle"                  # Vehicle Counting only counts vehicles

    count = to_int(first(data, "Count", "CurrentCount", "ObjectCount"))
    object_ids = as_list(data.get("ObjectId"))
    if count is None and object_ids and (state or "true").lower() != "false":
        count = len(set(object_ids))             # one ObjectId per object that crossed

    table_sql = INSERT_BI if is_business_intelligence(analytic) else INSERT_ANALYTIC
    run(table_sql, (
        parse_time(payload.get("UtcTime")),
        t["camera"], t["mac"], t["app"], analytic,
        flat(first(src, "VideoSourceToken", "VideoSourceConfigToken",
                   "VideoSourceConfigurationToken")) or t["video_source"],
        rule_index, rule_name,
        flat(data.get("ReportType")) or t["report_type"],
        object_type, direction, count, state,
        json.dumps(src), json.dumps(data),
    ))


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        log.info("Connected to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        for t in TOPICS:
            client.subscribe(t, qos=1)
            log.info("Subscribed to %s", t)
    else:
        log.error("MQTT connect failed: %s", reason_code)


def on_message(client, userdata, msg):
    text = msg.payload.decode("utf-8", errors="replace")
    dups = DuplicateKeyTracker()
    try:
        parsed = json.loads(text, object_pairs_hook=dups)
        payload_json = json.dumps(parsed)
    except ValueError:
        parsed, payload_json = None, None  # plain text; still stored in 'payload'
    if dups.duplicates:
        log.info("Repeated keys %s in message on %s; kept all values",
                    sorted(dups.duplicates), msg.topic)
    t = parse_topic(msg.topic)
    run(INSERT_RAW, (msg.topic, t["camera"], t["event"], text, payload_json))
    try:
        record_analytic(t, parsed)
    except Exception as exc:  # never let one odd message stop the recorder
        log.warning("Could not parse analytic data (%s): %s", exc, text[:200])
    log.info("%s  %s", msg.topic, text[:120].replace("\n", " "))


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cam-ingest")
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.on_connect = on_connect
client.on_message = on_message
client.reconnect_delay_set(min_delay=1, max_delay=30)

while True:
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        break
    except Exception as exc:
        log.warning("Broker unreachable (%s); retrying in 5s", exc)
        time.sleep(5)

client.loop_forever()
