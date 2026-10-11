"""Dump raw Google Health API responses for the last night, to verify endpoints and parsers.

    python probe.py
"""
import asyncio
import json
from datetime import datetime

from vitals import config
from vitals.google_health import fetch_overnight


async def main() -> None:
    cfg = config.load()
    today = datetime.now(cfg.tz).date()
    snap, raw = await fetch_overnight(cfg, today)
    print("=== RAW ===")
    print(json.dumps(raw, indent=2)[:12000])
    print("=== PARSED ===")
    print(json.dumps(snap, indent=2))
    print("\nIf PARSED is empty/null but RAW has data, adjust the parse_* functions in vitals/google_health.py.")
    print("If RAW shows errors, check GOOGLE_HEALTH_BASE_URL, GH_TYPE_* and scopes in .env.")


if __name__ == "__main__":
    asyncio.run(main())
