"""Google Health API v4 client (Fitbit Air data).

UNVERIFIED: the endpoint path, filter syntax and response shapes below are my best
reading of the v4 docs, which I could not fetch from the build sandbox. Everything
shape-dependent lives in this file. Run `python probe.py` to dump raw responses from
your own account, then adjust `_list_points` / the parse_* functions if needed. Raw
payloads are stored in biometric_snapshots.raw_json so nothing is lost meanwhile.
"""
import json
import time as _time
from datetime import datetime, timedelta
from typing import Any

import httpx

from .config import Config

TOKEN_URL = "https://oauth2.googleapis.com/token"
STAGES = {"DEEP": "deep_mins", "REM": "rem_mins", "LIGHT": "light_mins",
          "AWAKE": "awake_mins", "WAKE": "awake_mins"}


# ---------- auth ----------

def load_token(cfg: Config) -> dict:
    if not cfg.google_token_path.exists():
        raise RuntimeError("No Google token. Run `python auth.py` first.")
    return json.loads(cfg.google_token_path.read_text())


def save_token(cfg: Config, token: dict) -> None:
    cfg.google_token_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.google_token_path.write_text(json.dumps(token))
    cfg.google_token_path.chmod(0o600)


async def access_token(cfg: Config, client: httpx.AsyncClient) -> str:
    token = load_token(cfg)
    if token.get("access_token") and token.get("expires_at", 0) > _time.time() + 60:
        return token["access_token"]
    resp = await client.post(TOKEN_URL, data={
        "client_id": cfg.google_client_id,
        "client_secret": cfg.google_client_secret,
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    fresh = resp.json()
    token["access_token"] = fresh["access_token"]
    token["expires_at"] = _time.time() + int(fresh.get("expires_in", 3600))
    save_token(cfg, token)
    return token["access_token"]


# ---------- raw fetch ----------

async def _list_points(cfg: Config, client: httpx.AsyncClient, data_type: str,
                       start: datetime, end: datetime) -> dict:
    """GET {base}/users/me/dataTypes/{type}/dataPoints with a time window. UNVERIFIED shape."""
    token = await access_token(cfg, client)
    resp = await client.get(
        f"{cfg.gh_base_url}/users/me/dataTypes/{data_type}/dataPoints",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "filter": f'interval.start_time >= "{start.isoformat()}" AND interval.start_time < "{end.isoformat()}"',
            "pageSize": 100,
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------- parsing (defensive: shapes are unverified) ----------

def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _walk(node: Any):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _first_number(node: Any, keys: tuple[str, ...]) -> float | None:
    for n in _walk(node):
        if isinstance(n, dict):
            for k in keys:
                v = n.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v)
                    except ValueError:
                        pass
    return None


def _stage_minutes(stage: dict) -> float | None:
    start = _parse_ts(stage.get("startTime") or stage.get("start_time"))
    end = _parse_ts(stage.get("endTime") or stage.get("end_time"))
    if start and end:
        return (end - start).total_seconds() / 60
    for key, scale in (("durationMinutes", 1), ("minutes", 1), ("durationMillis", 1 / 60000),
                       ("durationSeconds", 1 / 60), ("seconds", 1 / 60)):
        if isinstance(stage.get(key), (int, float)):
            return stage[key] * scale
    return None


def parse_sleep(payload: dict) -> dict:
    """Pick the longest session in the window and total its stages."""
    points = payload.get("dataPoints") or payload.get("data_points") or []
    best: dict | None = None
    for point in points:
        stages = [n for n in _walk(point) if isinstance(n, dict)
                  and str(n.get("type") or n.get("stage") or "").upper() in STAGES]
        totals = {col: 0.0 for col in set(STAGES.values())}
        for s in stages:
            mins = _stage_minutes(s)
            if mins:
                totals[STAGES[str(s.get("type") or s.get("stage")).upper()]] += mins
        start = end = None
        for n in _walk(point):
            if isinstance(n, dict):
                start = start or _parse_ts(n.get("startTime") or n.get("start_time"))
                end = end or _parse_ts(n.get("endTime") or n.get("end_time"))
        asleep = totals["deep_mins"] + totals["rem_mins"] + totals["light_mins"]
        if not asleep and start and end:
            asleep = (end - start).total_seconds() / 60
        cand = {**totals, "total_sleep_mins": asleep or None,
                "sleep_start": start.isoformat() if start else None,
                "sleep_end": end.isoformat() if end else None}
        if best is None or (cand["total_sleep_mins"] or 0) > (best["total_sleep_mins"] or 0):
            best = cand
    if not best:
        return {}
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in best.items()}


def _latest_value(payload: dict, keys: tuple[str, ...]) -> float | None:
    points = payload.get("dataPoints") or payload.get("data_points") or []
    for point in reversed(points):
        v = _first_number(point, keys)
        if v is not None:
            return v
    return None


def parse_rhr(payload: dict) -> float | None:
    return _latest_value(payload, ("restingHeartRate", "resting_heart_rate", "beatsPerMinute",
                                   "bpm", "value"))


def parse_hrv(payload: dict) -> float | None:
    return _latest_value(payload, ("rmssd", "rmssdMillis", "dailyRmssd", "averageRmssd", "value"))


# ---------- public ----------

async def fetch_overnight(cfg: Config, wake_date) -> tuple[dict, dict]:
    """Fetch sleep + RHR + HRV for the night ending on `wake_date` (a local date).

    Window: previous day 12:00 -> wake_date 12:00 local, which captures an overnight session.
    Returns (snapshot_columns, raw_payloads).
    """
    end = datetime.combine(wake_date, datetime.min.time(), cfg.tz) + timedelta(hours=12)
    start = end - timedelta(hours=24)
    raw: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for name, dtype in (("sleep", cfg.gh_type_sleep), ("rhr", cfg.gh_type_rhr),
                            ("hrv", cfg.gh_type_hrv)):
            try:
                raw[name] = await _list_points(cfg, client, dtype, start, end)
            except httpx.HTTPError as exc:
                raw[name] = {"error": str(exc)}
    snap = parse_sleep(raw["sleep"]) if "error" not in raw["sleep"] else {}
    snap["rhr"] = parse_rhr(raw["rhr"]) if "error" not in raw["rhr"] else None
    snap["hrv_rmssd"] = parse_hrv(raw["hrv"]) if "error" not in raw["hrv"] else None
    return snap, raw
