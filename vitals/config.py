import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class Config:
    telegram_token: str
    owner_id: int | None
    tz: ZoneInfo
    morning_time: time
    evening_time: time
    google_client_id: str
    google_client_secret: str
    google_scopes: list[str]
    gh_base_url: str
    gh_type_sleep: str
    gh_type_rhr: str
    gh_type_hrv: str
    anthropic_api_key: str
    anthropic_model: str
    openai_api_key: str
    whisper_model: str
    db_path: Path
    google_token_path: Path


def load() -> Config:
    env = os.environ.get
    owner = env("TELEGRAM_OWNER_ID", "").strip()
    return Config(
        telegram_token=env("TELEGRAM_BOT_TOKEN", ""),
        owner_id=int(owner) if owner else None,
        tz=ZoneInfo(env("TZ", "UTC")),
        morning_time=_time(env("MORNING_TIME", "08:15")),
        evening_time=_time(env("EVENING_TIME", "17:45")),
        google_client_id=env("GOOGLE_CLIENT_ID", ""),
        google_client_secret=env("GOOGLE_CLIENT_SECRET", ""),
        google_scopes=env(
            "GOOGLE_SCOPES",
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly "
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        ).split(),
        gh_base_url=env("GOOGLE_HEALTH_BASE_URL", "https://health.googleapis.com/v4").rstrip("/"),
        gh_type_sleep=env("GH_TYPE_SLEEP", "sleep"),
        gh_type_rhr=env("GH_TYPE_RHR", "daily-resting-heart-rate"),
        gh_type_hrv=env("GH_TYPE_HRV", "daily-heart-rate-variability"),
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        anthropic_model=env("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        openai_api_key=env("OPENAI_API_KEY", ""),
        whisper_model=env("WHISPER_MODEL", "whisper-1"),
        db_path=Path(env("DB_PATH", "data/vitals.db")),
        google_token_path=Path(env("GOOGLE_TOKEN_PATH", "data/google_token.json")),
    )
