-- Runs once, the first time the database container starts with an empty volume.
-- (The ingest service also creates these with IF NOT EXISTS, so upgrades are safe.)

-- 1) Raw archive: every MQTT message, exactly as received
CREATE TABLE IF NOT EXISTS mqtt_messages (
    id           BIGSERIAL PRIMARY KEY,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- when the message arrived
    topic        TEXT        NOT NULL,                -- full MQTT topic
    camera       TEXT,                                -- friendly name or prefix
    event        TEXT,                                -- topic path, e.g. PeopleCounting/PeopleCountPunctual
    payload      TEXT,                                -- raw payload, always kept
    payload_json JSONB                                -- parsed copy if payload was JSON
);
CREATE INDEX IF NOT EXISTS idx_msgs_time   ON mqtt_messages (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_msgs_topic  ON mqtt_messages (topic, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_msgs_camera ON mqtt_messages (camera, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_msgs_json   ON mqtt_messages USING GIN (payload_json);

-- 2) Business Intelligence: people / vehicle / object counting, queue management, heatmap
-- 3) Analytics: line crossing, loitering, intrusion, ...
-- Same columns, separate tables: the two kinds of data are never mixed.
CREATE TABLE IF NOT EXISTS bi_events (
    id           BIGSERIAL PRIMARY KEY,
    event_time   TIMESTAMPTZ NOT NULL,   -- camera's UtcTime (falls back to arrival time)
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    camera       TEXT,
    mac          TEXT,
    app          TEXT,                   -- WiseAI, Built-in, RuleEngine
    analytic     TEXT,                   -- e.g. PeopleCounting, VehicleCounting
    video_source TEXT,
    rule_index   INT,
    rule_name    TEXT,                   -- counting line / queue name
    report_type  TEXT,                   -- e.g. PeopleCountPunctual
    object_type  TEXT,                   -- Person, Vehicle
    direction    TEXT,                   -- IN / OUT
    count        INT,                    -- crossings, or current queue length
    state        TEXT,
    source       JSONB,
    data         JSONB
);
CREATE TABLE IF NOT EXISTS analytics_events (
    id           BIGSERIAL PRIMARY KEY,
    event_time   TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    camera       TEXT,
    mac          TEXT,
    app          TEXT,                   -- VideoAnalytics, WiseAI
    analytic     TEXT,                   -- e.g. LineCrossing, Loitering
    video_source TEXT,
    rule_index   INT,                    -- line / area number
    rule_name    TEXT,                   -- e.g. "Line 1"
    report_type  TEXT,
    object_type  TEXT,
    direction    TEXT,                   -- LEFT / RIGHT for line crossing
    count        INT,                    -- objects in the event
    state        TEXT,                   -- true / false
    source       JSONB,
    data         JSONB
);
CREATE INDEX IF NOT EXISTS idx_bi_time   ON bi_events (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_bi_lookup ON bi_events (analytic, camera, rule_name, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_an_time   ON analytics_events (event_time DESC);
CREATE INDEX IF NOT EXISTS idx_an_lookup ON analytics_events (analytic, camera, rule_name, event_time DESC);
