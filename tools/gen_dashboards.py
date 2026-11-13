"""Generates the three Grafana dashboards (BI, Analytics, Raw).

Usage (from the project folder):  python3 tools/gen_dashboards.py grafana/dashboards
Edit this script, not the JSON files - the JSON is regenerated from it.
"""
import json, os, sys

OUT = sys.argv[1]
ds = {"type": "grafana-postgresql-datasource", "uid": "camdb"}
bars = {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 1,
                                "stacking": {"mode": "normal"}}}}


def filt(c="camera", a="analytic", r="rule"):
    return (f"('__all' IN (${c}) OR camera IN (${c})) AND ('__all' IN (${a}) OR analytic IN (${a})) "
            f"AND ('__all' IN (${r}) OR rule_name IN (${r}))")


class Dash:
    def __init__(self):
        self.panels, self.id, self.y = [], 0, 0

    def row(self, title):
        self.id += 1
        self.panels.append({"id": self.id, "type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.y += 1

    def p(self, type, title, x, w, h, sql, fmt="table", newline=False, **extra):
        self.id += 1
        q = {"id": self.id, "type": type, "title": title,
             "gridPos": {"x": x, "y": self.y, "w": w, "h": h}, "datasource": ds,
             "targets": [{"refId": "A", "datasource": ds, "rawQuery": True, "editorMode": "code",
                          "format": fmt, "rawSql": sql}]}
        q.update(extra)
        self.panels.append(q)
        if x + w >= 24:
            self.y += h


def variables(table):
    def var(name, label, sql):
        return {"name": name, "label": label, "type": "query", "datasource": ds, "query": sql,
                "definition": sql, "multi": True, "includeAll": True, "allValue": "'__all'",
                "refresh": 2, "sort": 1, "current": {"text": ["All"], "value": ["$__all"]}}
    cam = "('__all' IN ($camera) OR camera IN ($camera))"
    an = "('__all' IN ($analytic) OR analytic IN ($analytic))"
    return {"list": [
        var("camera", "Camera", f"SELECT DISTINCT camera FROM {table} WHERE camera IS NOT NULL ORDER BY 1"),
        var("analytic", "Type", f"SELECT DISTINCT analytic FROM {table} WHERE analytic IS NOT NULL AND {cam} ORDER BY 1"),
        var("rule", "Rule / line", f"SELECT DISTINCT rule_name FROM {table} WHERE rule_name IS NOT NULL AND {cam} AND {an} ORDER BY 1"),
    ]}


def dashboard(uid, title, panels, templating=None):
    d = {"title": title, "uid": uid, "schemaVersion": 39, "refresh": "30s", "tags": ["hanwha"],
         "time": {"from": "now-24h", "to": "now"}, "panels": panels,
         "links": [{"title": "Hanwha dashboards", "type": "dashboards", "tags": ["hanwha"],
                    "asDropdown": False, "includeVars": False, "keepTime": True}]}
    if templating:
        d["templating"] = templating
    return d


# "Last ..." stat panels show a timestamp as "x minutes ago". A stat panel only
# shows number fields unless told otherwise, so pick the "Last" field explicitly.
# Fixed colour: the default thresholds would turn any timestamp red.
LAST_TIME = dict(fieldConfig={"defaults": {"unit": "dateTimeFromNow", "color": {"mode": "fixed", "fixedColor": "green"}}},
                 options={"reduceOptions": {"values": False, "calcs": ["lastNotNull"],
                                            "fields": "/^Last$/"}})


# ---------------------------------------------------------------- Business Intelligence
T = "bi_events"
F = filt()
COUNT = ("count IS NOT NULL AND direction IN ('IN','OUT') "
         "AND (report_type IS NULL OR report_type ILIKE '%punctual%')")
QUEUE = "analytic ILIKE '%queue%' AND count IS NOT NULL"
LBL = "camera || ' · ' || coalesce(rule_name,'queue')"
b = Dash()
b.row("Overview")
b.p("stat", "BI events", 0, 6, 4, f"SELECT count(*) AS \"Events\" FROM {T} WHERE $__timeFilter(event_time) AND {F}")
b.p("stat", "Cameras reporting", 6, 6, 4, f"SELECT count(DISTINCT camera) AS \"Cameras\" FROM {T} WHERE $__timeFilter(event_time) AND {F}")
b.p("stat", "Last BI event", 12, 12, 4, f"SELECT max(event_time) AS \"Last\" FROM {T} WHERE {F}",
    **LAST_TIME)
b.row("Counting (People, Vehicle, WiseAI Object Counting)")
b.p("stat", "Total IN", 0, 8, 4, f"SELECT coalesce(sum(count),0) AS \"IN\" FROM {T} WHERE direction='IN' AND {COUNT} AND $__timeFilter(event_time) AND {F}")
b.p("stat", "Total OUT", 8, 8, 4, f"SELECT coalesce(sum(count),0) AS \"OUT\" FROM {T} WHERE direction='OUT' AND {COUNT} AND $__timeFilter(event_time) AND {F}")
b.p("stat", "Net (IN − OUT)", 16, 8, 4,
    f"SELECT coalesce(sum(CASE direction WHEN 'IN' THEN count ELSE -count END),0) AS \"Net\"\nFROM {T} WHERE {COUNT} AND $__timeFilter(event_time) AND {F}")
b.p("timeseries", "Counts over time by object type & direction", 0, 24, 9,
    f"SELECT $__timeGroupAlias(event_time, $__interval), coalesce(object_type,'Object') || ' ' || direction AS metric, sum(count) AS value\n"
    f"FROM {T} WHERE {COUNT} AND $__timeFilter(event_time) AND {F}\nGROUP BY 1, 2 ORDER BY 1",
    "time_series", fieldConfig=bars, interval="15m")
b.p("table", "Totals by camera, counting rule & object type", 0, 24, 8,
    f"SELECT camera, analytic AS type, rule_name AS rule, coalesce(object_type,'Object') AS object_type,\n"
    f"  sum(count) FILTER (WHERE direction='IN')  AS \"IN\",\n"
    f"  sum(count) FILTER (WHERE direction='OUT') AS \"OUT\",\n  sum(count) AS total\n"
    f"FROM {T} WHERE {COUNT} AND $__timeFilter(event_time) AND {F}\nGROUP BY 1,2,3,4 ORDER BY total DESC")
b.row("Queue management")
b.p("bargauge", "Current queue length", 0, 8, 8,
    f"SELECT DISTINCT ON (camera, rule_name) {LBL} AS queue, count AS \"people\"\nFROM {T} WHERE {QUEUE} AND {F}\nORDER BY camera, rule_name, event_time DESC",
    options={"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
             "reduceOptions": {"values": True, "calcs": ["lastNotNull"], "fields": ""}},
    fieldConfig={"defaults": {"min": 0, "thresholds": {"mode": "absolute", "steps": [
        {"color": "green", "value": None}, {"color": "yellow", "value": 5}, {"color": "red", "value": 10}]}}})
b.p("timeseries", "Queue length over time", 8, 16, 8,
    f"SELECT event_time AS time, {LBL} AS metric, count AS value\nFROM {T} WHERE $__timeFilter(event_time) AND {QUEUE} AND {F}\nORDER BY 1",
    "time_series", fieldConfig={"defaults": {"min": 0, "custom": {"lineInterpolation": "stepAfter", "lineWidth": 2,
                                                                "fillOpacity": 10, "showPoints": "never"}}})
b.p("table", "Queue summary (selected time range)", 0, 24, 6,
    f"SELECT {LBL} AS queue,\n  (array_agg(count ORDER BY event_time DESC))[1] AS \"current\",\n  max(count) AS \"peak\",\n"
    f"  count(*) AS \"changes\",\n  max(event_time) AS \"last change\"\nFROM {T} WHERE $__timeFilter(event_time) AND {QUEUE} AND {F}\nGROUP BY 1 ORDER BY \"peak\" DESC")
CROWD = "analytic ILIKE '%crowd%' AND count IS NOT NULL"
CL = "camera || ' · ' || coalesce(rule_name,'area')"
b.row("Crowd count")
b.p("bargauge", "Current crowd count", 0, 8, 8,
    f"SELECT DISTINCT ON (camera, rule_name) {CL} AS area, count AS \"people\"\nFROM {T} WHERE {CROWD} AND {F}\nORDER BY camera, rule_name, event_time DESC",
    options={"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
             "reduceOptions": {"values": True, "calcs": ["lastNotNull"], "fields": ""}},
    fieldConfig={"defaults": {"min": 0, "thresholds": {"mode": "absolute", "steps": [
        {"color": "green", "value": None}, {"color": "yellow", "value": 20}, {"color": "red", "value": 50}]}}})
b.p("timeseries", "Crowd count over time", 8, 16, 8,
    f"SELECT event_time AS time, {CL} AS metric, count AS value\nFROM {T} WHERE $__timeFilter(event_time) AND {CROWD} AND {F}\nORDER BY 1",
    "time_series", fieldConfig={"defaults": {"min": 0, "custom": {"lineInterpolation": "stepAfter", "lineWidth": 2,
                                                                "fillOpacity": 10, "showPoints": "never"}}})
b.p("table", "Crowd summary (selected time range)", 0, 24, 6,
    f"SELECT {CL} AS area,\n  (array_agg(count ORDER BY event_time DESC))[1] AS \"current\",\n  max(count) AS \"peak\",\n"
    f"  (array_agg(event_time ORDER BY count DESC, event_time DESC))[1] AS \"peak time\",\n"
    f"  count(*) AS \"changes\",\n  max(event_time) AS \"last change\"\nFROM {T} WHERE $__timeFilter(event_time) AND {CROWD} AND {F}\nGROUP BY 1 ORDER BY \"peak\" DESC")
b.row("Latest BI events")
b.p("table", "Latest BI events", 0, 24, 9,
    f"SELECT event_time AS time, camera, analytic AS type, rule_name AS rule, object_type, direction, count, data::text AS data\n"
    f"FROM {T} WHERE $__timeFilter(event_time) AND {F}\nORDER BY event_time DESC LIMIT 200")
bi = dashboard("hanwha-bi", "Hanwha – Business Intelligence", b.panels, variables(T))

# ---------------------------------------------------------------- Analytics
T = "analytics_events"
LINE = "analytic = 'LineCrossing' AND count IS NOT NULL"
LL = "camera || ' · ' || coalesce(rule_name,'line')"
a = Dash()
a.row("Overview")
a.p("stat", "Analytics events", 0, 6, 4, f"SELECT count(*) AS \"Events\" FROM {T} WHERE $__timeFilter(event_time) AND {F}")
a.p("stat", "Cameras reporting", 6, 6, 4, f"SELECT count(DISTINCT camera) AS \"Cameras\" FROM {T} WHERE $__timeFilter(event_time) AND {F}")
a.p("stat", "Last analytics event", 12, 12, 4, f"SELECT max(event_time) AS \"Last\" FROM {T} WHERE {F}",
    **LAST_TIME)
a.p("timeseries", "Events over time by type", 0, 16, 8,
    f"SELECT $__timeGroupAlias(event_time, $__interval), analytic AS metric, count(*) AS value\n"
    f"FROM {T} WHERE $__timeFilter(event_time) AND {F}\nGROUP BY 1, 2 ORDER BY 1",
    "time_series", fieldConfig=bars, interval="5m")
a.p("barchart", "Events by camera / type / rule", 16, 8, 8,
    f"SELECT camera || ' · ' || analytic || ' · ' || coalesce(rule_name,'-') AS rule, count(*) AS events\n"
    f"FROM {T} WHERE $__timeFilter(event_time) AND {F}\nGROUP BY 1 ORDER BY events DESC LIMIT 25",
    options={"orientation": "horizontal"})
a.row("Line crossing")
a.p("stat", "Objects crossed", 0, 8, 4, f"SELECT coalesce(sum(count),0) AS \"Crossed\" FROM {T} WHERE {LINE} AND $__timeFilter(event_time) AND {F}")
a.p("stat", "Crossed LEFT", 8, 8, 4, f"SELECT coalesce(sum(count),0) AS \"LEFT\" FROM {T} WHERE direction='LEFT' AND {LINE} AND $__timeFilter(event_time) AND {F}")
a.p("stat", "Crossed RIGHT", 16, 8, 4, f"SELECT coalesce(sum(count),0) AS \"RIGHT\" FROM {T} WHERE direction='RIGHT' AND {LINE} AND $__timeFilter(event_time) AND {F}")
a.p("timeseries", "Crossings over time by line & direction", 0, 24, 9,
    f"SELECT $__timeGroupAlias(event_time, $__interval), {LL} || ' ' || coalesce(direction,'') AS metric, sum(count) AS value\n"
    f"FROM {T} WHERE {LINE} AND $__timeFilter(event_time) AND {F}\nGROUP BY 1, 2 ORDER BY 1",
    "time_series", fieldConfig=bars, interval="15m")
a.p("table", "Crossings by line", 0, 24, 7,
    f"SELECT camera, rule_name AS line,\n  sum(count) FILTER (WHERE direction='LEFT')  AS \"LEFT\",\n"
    f"  sum(count) FILTER (WHERE direction='RIGHT') AS \"RIGHT\",\n  sum(count) AS total,\n  max(event_time) AS \"last crossing\"\n"
    f"FROM {T} WHERE {LINE} AND $__timeFilter(event_time) AND {F}\nGROUP BY 1,2 ORDER BY total DESC")
AREA = "analytic <> 'LineCrossing' AND analytic NOT ILIKE '%slip%'"
AL = "camera || ' · ' || coalesce(rule_name,'-')"
a.row("Area events (Loitering, Intrusion, ...)")
a.p("stat", "Area events", 0, 8, 4, f"SELECT count(*) AS \"Events\" FROM {T} WHERE {AREA} AND (state IS NULL OR state <> 'false') AND $__timeFilter(event_time) AND {F}")
a.p("stat", "Loitering events", 8, 8, 4, f"SELECT count(*) AS \"Loitering\" FROM {T} WHERE analytic ILIKE 'loiter%' AND (state IS NULL OR state <> 'false') AND $__timeFilter(event_time) AND {F}")
a.p("stat", "Last area event", 16, 8, 4, f"SELECT max(event_time) AS \"Last\" FROM {T} WHERE {AREA} AND {F}",
    **LAST_TIME)
a.p("timeseries", "Area events over time by area & type", 0, 24, 8,
    f"SELECT $__timeGroupAlias(event_time, $__interval), {AL} || ' ' || analytic AS metric, count(*) AS value\n"
    f"FROM {T} WHERE {AREA} AND (state IS NULL OR state <> 'false') AND $__timeFilter(event_time) AND {F}\nGROUP BY 1, 2 ORDER BY 1",
    "time_series", fieldConfig=bars, interval="15m")
a.p("table", "Area events by area", 0, 24, 7,
    f"SELECT camera, rule_name AS area, analytic AS type,\n  count(*) AS events,\n  sum(count) AS objects,\n  max(event_time) AS \"last event\"\n"
    f"FROM {T} WHERE {AREA} AND (state IS NULL OR state <> 'false') AND $__timeFilter(event_time) AND {F}\nGROUP BY 1,2,3 ORDER BY events DESC")
SLIP = "analytic ILIKE '%slip%' AND (state IS NULL OR lower(state) <> 'false')"
a.row("Slip and fall")
a.p("stat", "Slip & fall triggers", 0, 8, 4, f"SELECT count(*) AS \"Triggers\" FROM {T} WHERE {SLIP} AND $__timeFilter(event_time) AND {F}",
    fieldConfig={"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}}})
a.p("stat", "Cameras with triggers", 8, 8, 4, f"SELECT count(DISTINCT camera) AS \"Cameras\" FROM {T} WHERE {SLIP} AND $__timeFilter(event_time) AND {F}")
a.p("stat", "Last slip & fall", 16, 8, 4, f"SELECT max(event_time) AS \"Last\" FROM {T} WHERE {SLIP} AND {F}",
    **LAST_TIME)
a.p("timeseries", "Slip & fall triggers over time by camera & rule", 0, 16, 8,
    f"SELECT $__timeGroupAlias(event_time, $__interval), camera || ' · ' || coalesce(rule_name,'-') AS metric, count(*) AS value\n"
    f"FROM {T} WHERE {SLIP} AND $__timeFilter(event_time) AND {F}\nGROUP BY 1, 2 ORDER BY 1",
    "time_series", fieldConfig=bars, interval="1h")
a.p("table", "Triggers by camera & rule", 16, 8, 8,
    f"SELECT camera, rule_name AS rule, count(*) AS triggers, max(event_time) AS \"last trigger\"\n"
    f"FROM {T} WHERE {SLIP} AND $__timeFilter(event_time) AND {F}\nGROUP BY 1,2 ORDER BY triggers DESC")
a.row("Latest analytics events")
a.p("table", "Latest analytics events", 0, 24, 9,
    f"SELECT event_time AS time, camera, analytic AS type, rule_name AS rule, object_type, direction, count, state, data::text AS data\n"
    f"FROM {T} WHERE $__timeFilter(event_time) AND {F}\nORDER BY event_time DESC LIMIT 200")
an = dashboard("hanwha-analytics", "Hanwha – Analytics", a.panels, variables(T))

# ---------------------------------------------------------------- Raw
r = Dash()
r.p("stat", "Messages received", 0, 6, 4, "SELECT count(*) AS \"Messages\" FROM mqtt_messages WHERE $__timeFilter(received_at)")
r.p("stat", "Last message", 6, 18, 4, "SELECT max(received_at) AS \"Last\" FROM mqtt_messages",
    **LAST_TIME)
r.p("timeseries", "Messages over time by topic type", 0, 24, 8,
    "SELECT $__timeGroupAlias(received_at, $__interval), event AS metric, count(*) AS value\n"
    "FROM mqtt_messages WHERE $__timeFilter(received_at)\nGROUP BY 1, 2 ORDER BY 1",
    "time_series", fieldConfig=bars, interval="5m")
r.p("table", "Latest raw messages", 0, 24, 12,
    "SELECT received_at AS time, camera, event, topic, payload\nFROM mqtt_messages WHERE $__timeFilter(received_at)\n"
    "ORDER BY received_at DESC LIMIT 200")
raw = dashboard("hanwha-raw", "Hanwha – Raw MQTT messages", r.panels)

for name, d in [("hanwha-bi.json", bi), ("hanwha-analytics.json", an), ("hanwha-raw.json", raw)]:
    json.dump(d, open(os.path.join(OUT, name), "w"), indent=2)
    print(name, len(d["panels"]), "panels")
