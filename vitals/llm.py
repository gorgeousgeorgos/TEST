"""Turn free-form text/voice dumps into structured input_events via Anthropic tool calling."""
import re
from datetime import datetime, timedelta

import anthropic

from .config import Config

TOOL = {
    "name": "log_events",
    "description": "Record every concrete habit or action the user mentions. One entry per distinct item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "enum": ["health", "business"]},
                        "category": {"type": "string",
                                     "description": "substance | nutrition | supplement | screen | exercise | outreach | content | engineering | ops | other"},
                        "metric_name": {"type": "string",
                                        "description": "snake_case, reuse the vocabulary in the system prompt"},
                        "metric_value": {"type": "number"},
                        "event_time": {"type": "string", "description": "Approx local time HH:MM (24h) if stated or implied, else omit"},
                        "day": {"type": "string", "enum": ["today", "yesterday"],
                                "description": "Which day the event belongs to"},
                    },
                    "required": ["domain", "category", "metric_name", "metric_value", "day"],
                },
            }
        },
        "required": ["events"],
    },
}

SYSTEM = """You extract structured log entries from a person's casual messages (typed or transcribed voice).
Call log_events exactly once. If nothing loggable is mentioned, call it with an empty list.

Reuse these metric_names where they fit (invent new snake_case ones only when needed):
health: alcohol_units (1 beer/glass of wine = 1), caffeine_drinks, last_meal (value 1, event_time = when), late_snack (1),
  meal_heavy (1), supplement_<name> (1 per supplement, e.g. supplement_magnesium), screen_cutoff (value 1, event_time = when screens stopped),
  screen_minutes_in_bed, workout_minutes, sauna_minutes, stress_level (1-5)
business: linkedin_dms, connection_requests, linkedin_posts, cold_emails, calls_booked, sales_calls, deep_work_hours,
  engineering_hours, admin_hours, ops_win (1 each, describe nothing else), ops_drag (1 each)

Rules:
- Numbers: "3 beers" -> alcohol_units 3. "about two hours of deep work" -> deep_work_hours 2.
- Times: "dinner at 9" in an evening context means 21:00. Output 24h HH:MM.
- day: use the conversation context below. A reply to the MORNING prompt is about last evening/night -> "yesterday" for
  evening habits, unless the user says otherwise. A reply to the EVENING prompt is about today -> "today".
- Do not invent entries. Do not log things the user says they did NOT do, unless the count is meaningful (e.g. 0 alcohol_units is fine when they say "no alcohol")."""

METRIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def normalize_events(raw_events: list[dict], today: datetime) -> list[dict]:
    """Validate model output and resolve `day` to a concrete log_date. Drops malformed entries."""
    out = []
    for e in raw_events or []:
        try:
            if e["domain"] not in ("health", "business"):
                continue
            name = str(e["metric_name"]).strip().lower().replace(" ", "_")
            if not METRIC_RE.match(name):
                continue
            value = float(e["metric_value"])
        except (KeyError, TypeError, ValueError):
            continue
        event_time = e.get("event_time")
        if event_time is not None:
            event_time = str(event_time).strip()
            if not TIME_RE.match(event_time):
                event_time = None
            else:
                h, m = event_time.split(":")
                event_time = f"{int(h):02d}:{m}"
        day = today.date() - timedelta(days=1) if e.get("day") == "yesterday" else today.date()
        out.append({
            "domain": e["domain"],
            "category": str(e.get("category") or "other").strip().lower()[:32],
            "metric_name": name,
            "metric_value": value,
            "event_time": event_time,
            "log_date": day.isoformat(),
        })
    return out


async def extract(cfg: Config, text: str, now: datetime, last_prompt: str | None) -> list[dict]:
    if not cfg.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    context = (f"Now: {now:%A %Y-%m-%d %H:%M} ({cfg.tz.key}). "
               f"The user is replying to the {(last_prompt or 'unknown').upper()} prompt.")
    msg = await client.messages.create(
        model=cfg.anthropic_model,
        max_tokens=1024,
        system=f"{SYSTEM}\n\nContext: {context}",
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "log_events"},
        messages=[{"role": "user", "content": text}],
    )
    for block in msg.content:
        if block.type == "tool_use" and block.name == "log_events":
            return normalize_events(block.input.get("events", []), now)
    return []
