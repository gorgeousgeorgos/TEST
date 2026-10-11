import json
import sqlite3
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

SNAPSHOT_COLS = [
    "sleep_start", "sleep_end", "total_sleep_mins", "deep_mins", "rem_mins",
    "light_mins", "awake_mins", "hrv_rmssd", "rhr",
]


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_snapshot(conn, log_date: str, snap: dict, raw: dict | None = None) -> None:
    values = [snap.get(c) for c in SNAPSHOT_COLS]
    placeholders = ", ".join("?" for _ in SNAPSHOT_COLS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in SNAPSHOT_COLS)
    conn.execute(
        f"""INSERT INTO biometric_snapshots (log_date, {', '.join(SNAPSHOT_COLS)}, raw_json)
            VALUES (?, {placeholders}, ?)
            ON CONFLICT(log_date) DO UPDATE SET {updates}, raw_json=excluded.raw_json,
                                               fetched_at=datetime('now')""",
        [log_date, *values, json.dumps(raw) if raw is not None else None],
    )
    conn.commit()


def get_snapshot(conn, log_date: str):
    return conn.execute(
        "SELECT * FROM biometric_snapshots WHERE log_date = ?", (log_date,)
    ).fetchone()


def baseline(conn, column: str, before_date: str, days: int = 14) -> float | None:
    """Mean of a snapshot column over the `days` rows before `before_date`."""
    if column not in SNAPSHOT_COLS:
        raise ValueError(column)
    row = conn.execute(
        f"""SELECT AVG({column}) AS m FROM (
                SELECT {column} FROM biometric_snapshots
                WHERE log_date < ? AND {column} IS NOT NULL
                ORDER BY log_date DESC LIMIT ?)""",
        (before_date, days),
    ).fetchone()
    return row["m"]


def insert_events(conn, events: list[dict], ts: str, raw_text: str, source: str) -> int:
    """events carry: log_date, domain, category, metric_name, metric_value, event_time."""
    conn.executemany(
        """INSERT INTO input_events
           (ts, log_date, domain, category, metric_name, metric_value, event_time, raw_text, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (ts, e["log_date"], e["domain"], e["category"], e["metric_name"],
             e["metric_value"], e.get("event_time"), raw_text, source)
            for e in events
        ],
    )
    conn.commit()
    return len(events)


def undo_last_batch(conn) -> int:
    row = conn.execute("SELECT MAX(ts) AS ts FROM input_events").fetchone()
    if not row["ts"]:
        return 0
    cur = conn.execute("DELETE FROM input_events WHERE ts = ?", (row["ts"],))
    conn.commit()
    return cur.rowcount


def daily_totals(conn, log_date: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM daily_input_totals WHERE log_date = ? ORDER BY domain, category, metric_name",
        (log_date,),
    ).fetchall()


def kv_get(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
