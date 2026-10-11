"""Morning recovery brief: deterministic facts vs. your own 14-day baseline, phrased by an LLM if available."""
import anthropic

from . import db
from .config import Config

METRICS = [  # (column, label, unit, higher_is_better)
    ("hrv_rmssd", "HRV", "ms", True),
    ("rhr", "resting HR", "bpm", False),
    ("deep_mins", "deep sleep", "min", True),
    ("total_sleep_mins", "total sleep", "min", True),
]


def facts(conn, log_date: str) -> list[dict]:
    snap = db.get_snapshot(conn, log_date)
    if not snap:
        return []
    out = []
    for col, label, unit, higher_better in METRICS:
        value = snap[col]
        if value is None:
            continue
        base = db.baseline(conn, col, log_date)
        entry = {"label": label, "unit": unit, "value": round(value), "baseline": None, "delta_pct": None, "good": None}
        if base:
            delta = (value - base) / base * 100
            entry.update(baseline=round(base), delta_pct=round(delta),
                         good=(delta >= 0) == higher_better if abs(delta) >= 5 else None)
        out.append(entry)
    return out


def fallback_brief(fs: list[dict]) -> str:
    if not fs:
        return "No sleep data synced yet. Open the Google Health app so the tracker syncs, then send /brief."
    parts = []
    for f in fs:
        s = f"{f['label']} {f['value']}{f['unit']}"
        if f["delta_pct"] is not None:
            s += f" ({f['delta_pct']:+d}% vs your 14-day avg)"
        parts.append(s)
    return "Last night: " + ", ".join(parts) + "."


async def brief(cfg: Config, conn, log_date: str) -> str:
    fs = facts(conn, log_date)
    if not fs or not cfg.anthropic_api_key:
        return fallback_brief(fs)
    try:
        client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        msg = await client.messages.create(
            model=cfg.anthropic_model,
            max_tokens=200,
            system=("Write a recovery brief of exactly two short sentences from these numbers. "
                    "Sentence 1: how recovered the person is vs their own baseline. "
                    "Sentence 2: one concrete suggestion for today. No emojis, no medical claims, "
                    "do not invent numbers. If there is no baseline yet, say it is still learning."),
            messages=[{"role": "user", "content": str(fs)}],
        )
        return msg.content[0].text.strip()
    except anthropic.APIError:
        return fallback_brief(fs)
