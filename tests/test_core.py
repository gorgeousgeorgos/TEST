from datetime import datetime
from zoneinfo import ZoneInfo

from vitals import briefs, db, google_health, llm

NOW = datetime(2026, 10, 12, 8, 30, tzinfo=ZoneInfo("Europe/Berlin"))


def test_normalize_events_resolves_days_and_drops_junk():
    raw = [
        {"domain": "health", "category": "Substance", "metric_name": "Alcohol Units", "metric_value": "3",
         "event_time": "9:30", "day": "yesterday"},
        {"domain": "business", "category": "outreach", "metric_name": "linkedin_dms", "metric_value": 12, "day": "today"},
        {"domain": "nope", "category": "x", "metric_name": "x", "metric_value": 1, "day": "today"},
        {"domain": "health", "category": "x", "metric_name": "bad name!", "metric_value": 1, "day": "today"},
        {"domain": "health", "category": "x", "metric_name": "ok", "metric_value": "abc", "day": "today"},
        {"domain": "health", "category": "x", "metric_name": "t", "metric_value": 1, "event_time": "25:99", "day": "today"},
    ]
    out = llm.normalize_events(raw, NOW)
    assert [e["metric_name"] for e in out] == ["alcohol_units", "linkedin_dms", "t"]
    assert out[0]["log_date"] == "2026-10-11" and out[0]["event_time"] == "09:30" and out[0]["metric_value"] == 3.0
    assert out[1]["log_date"] == "2026-10-12"
    assert out[2]["event_time"] is None


def test_ledger_allows_repeats_and_view_aggregates():
    conn = db.connect(":memory:")
    ev = lambda t, v: {"log_date": "2026-10-11", "domain": "health", "category": "substance",
                       "metric_name": "alcohol_units", "metric_value": v, "event_time": t}
    db.insert_events(conn, [ev("19:00", 1), ev("22:30", 2)], "2026-10-11T20:00:00+00:00", "a beer then two", "text")
    rows = db.daily_totals(conn, "2026-10-11")
    assert len(rows) == 1 and rows[0]["total_value"] == 3 and rows[0]["n_events"] == 2
    assert rows[0]["earliest_time"] == "19:00" and rows[0]["latest_time"] == "22:30"
    assert conn.execute("SELECT COUNT(*) FROM input_events").fetchone()[0] == 2  # ledger not flattened
    assert db.undo_last_batch(conn) == 2


def test_snapshot_upsert_baseline_and_brief_fallback():
    conn = db.connect(":memory:")
    for d, hrv in (("2026-10-09", 50), ("2026-10-10", 60), ("2026-10-11", 55)):
        db.upsert_snapshot(conn, d, {"hrv_rmssd": hrv, "rhr": 55}, {})
    db.upsert_snapshot(conn, "2026-10-12", {"hrv_rmssd": 33, "rhr": 61, "deep_mins": 40}, {})
    assert db.baseline(conn, "hrv_rmssd", "2026-10-12") == 55
    text = briefs.fallback_brief(briefs.facts(conn, "2026-10-12"))
    assert "HRV 33ms (-40% vs your 14-day avg)" in text


def test_parse_sleep_stages_and_hrv_rhr():
    payload = {"dataPoints": [
        {"sleep": {"stages": [
            {"type": "LIGHT", "startTime": "2026-10-11T22:00:00Z", "endTime": "2026-10-12T01:00:00Z"},
            {"type": "DEEP", "startTime": "2026-10-12T01:00:00Z", "endTime": "2026-10-12T02:15:00Z"},
            {"type": "REM", "startTime": "2026-10-12T02:15:00Z", "endTime": "2026-10-12T03:00:00Z"},
            {"type": "AWAKE", "startTime": "2026-10-12T03:00:00Z", "endTime": "2026-10-12T03:10:00Z"},
        ]}},
        {"sleep": {"stages": [{"type": "LIGHT", "startTime": "2026-10-12T05:00:00Z", "endTime": "2026-10-12T05:20:00Z"}]}},
    ]}
    s = google_health.parse_sleep(payload)
    assert (s["deep_mins"], s["rem_mins"], s["light_mins"], s["awake_mins"]) == (75.0, 45.0, 180.0, 10.0)
    assert s["total_sleep_mins"] == 300.0
    assert google_health.parse_hrv({"dataPoints": [{"dailyHeartRateVariability": {"rmssd": 42.5}}]}) == 42.5
    assert google_health.parse_rhr({"dataPoints": [{"restingHeartRate": 54}]}) == 54.0
