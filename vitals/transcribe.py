import httpx

from .config import Config


async def transcribe(cfg: Config, audio: bytes, filename: str = "voice.ogg") -> str:
    """Whisper via the OpenAI API. Telegram voice notes are OGG/Opus, which the API accepts as-is."""
    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (needed for voice notes).")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
            data={"model": cfg.whisper_model},
            files={"file": (filename, audio, "audio/ogg")},
        )
    resp.raise_for_status()
    return resp.json()["text"].strip()
