-- One row per waking day. Sleep ending on the morning of log_date.
CREATE TABLE IF NOT EXISTS biometric_snapshots (
    log_date         TEXT PRIMARY KEY,   -- local date you woke up (YYYY-MM-DD)
    sleep_start      TEXT,
    sleep_end        TEXT,
    total_sleep_mins REAL,
    deep_mins        REAL,
    rem_mins         REAL,
    light_mins       REAL,
    awake_mins       REAL,
    hrv_rmssd        REAL,
    rhr              REAL,
    raw_json         TEXT,               -- raw API payloads, so parsers can be fixed later
    fetched_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only ledger. No uniqueness: two drinks at different times are two rows.
CREATE TABLE IF NOT EXISTS input_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,          -- UTC time the message was received (also the batch id)
    log_date     TEXT NOT NULL,          -- local date the event belongs to
    domain       TEXT NOT NULL CHECK (domain IN ('health', 'business')),
    category     TEXT NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value REAL NOT NULL,
    event_time   TEXT,                   -- approx local HH:MM the thing happened, if known
    raw_text     TEXT,
    source       TEXT NOT NULL DEFAULT 'text'   -- text | voice
);
CREATE INDEX IF NOT EXISTS idx_events_date ON input_events (log_date, domain);
CREATE INDEX IF NOT EXISTS idx_events_metric ON input_events (metric_name, log_date);

-- Aggregates without flattening the ledger.
CREATE VIEW IF NOT EXISTS daily_input_totals AS
SELECT log_date, domain, category, metric_name,
       SUM(metric_value) AS total_value,
       COUNT(*)          AS n_events,
       MIN(event_time)   AS earliest_time,
       MAX(event_time)   AS latest_time
FROM input_events
GROUP BY log_date, domain, category, metric_name;

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
