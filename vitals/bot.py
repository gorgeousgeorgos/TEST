import functools
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import briefs, db, llm
from . import config as config_mod
from .google_health import fetch_overnight
from .transcribe import transcribe

log = logging.getLogger("vitals")

MORNING_PROMPT = (
    "Last night, what did you do?\n"
    "• Last meal and when\n• Alcohol / caffeine\n• Supplements\n• When screens went off\n"
    "Text or voice note is fine."
)
EVENING_PROMPT = (
    "Business check-in:\n"
    "• Outbound touches (DMs, connection requests, posts, emails)\n"
    "• Deep work hours\n• Operational wins vs. drag\n"
    "Text or voice note is fine."
)


def owner_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cfg = ctx.bot_data["cfg"]
        if cfg.owner_id is None or update.effective_user is None or update.effective_user.id != cfg.owner_id:
            return
        return await fn(update, ctx)
    return wrapper


def _now(cfg) -> datetime:
    return datetime.now(cfg.tz)


async def sync_and_brief(ctx_app) -> str:
    cfg, conn = ctx_app.bot_data["cfg"], ctx_app.bot_data["conn"]
    today = _now(cfg).date()
    snap, raw = await fetch_overnight(cfg, today)
    if snap:
        db.upsert_snapshot(conn, today.isoformat(), snap, raw)
    return await briefs.brief(cfg, conn, today.isoformat())


# ---------- scheduled jobs ----------

async def morning_job(ctx: ContextTypes.DEFAULT_TYPE):
    cfg, conn = ctx.bot_data["cfg"], ctx.bot_data["conn"]
    try:
        text = await sync_and_brief(ctx.application)
    except Exception as exc:  # keep the ping alive even when Google is down
        log.exception("morning sync failed")
        text = f"Couldn't pull Google Health data ({type(exc).__name__}). Send /brief to retry."
    await ctx.bot.send_message(cfg.owner_id, text)
    await ctx.bot.send_message(cfg.owner_id, MORNING_PROMPT)
    db.kv_set(conn, "last_prompt", "morning")


async def evening_job(ctx: ContextTypes.DEFAULT_TYPE):
    cfg, conn = ctx.bot_data["cfg"], ctx.bot_data["conn"]
    await ctx.bot.send_message(cfg.owner_id, EVENING_PROMPT)
    db.kv_set(conn, "last_prompt", "evening")


# ---------- commands ----------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["cfg"]
    uid = update.effective_user.id
    if cfg.owner_id is None:
        await update.message.reply_text(
            f"Your Telegram user id is {uid}. Put TELEGRAM_OWNER_ID={uid} in .env and restart me.")
    elif uid == cfg.owner_id:
        await update.message.reply_text("Ready. /brief, /today, /undo, or just tell me what you did.")


@owner_only
async def cmd_brief(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(await sync_and_brief(ctx.application))
    except Exception as exc:
        log.exception("brief failed")
        await update.message.reply_text(f"Sync failed: {type(exc).__name__}: {exc}")


@owner_only
async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg, conn = ctx.bot_data["cfg"], ctx.bot_data["conn"]
    today = _now(cfg).date()
    lines = []
    for day in (today - timedelta(days=1), today):
        rows = db.daily_totals(conn, day.isoformat())
        if rows:
            lines.append(f"{day:%a %d %b}")
            lines += [f"  {r['metric_name']}: {r['total_value']:g}" + (f" ({r['n_events']}x)" if r["n_events"] > 1 else "")
                      for r in rows]
    await update.message.reply_text("\n".join(lines) or "Nothing logged for yesterday or today.")


@owner_only
async def cmd_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    n = db.undo_last_batch(ctx.bot_data["conn"])
    await update.message.reply_text(f"Removed {n} entries from your last message." if n else "Nothing to undo.")


# ---------- free-form input ----------

async def _process(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, source: str):
    cfg, conn = ctx.bot_data["cfg"], ctx.bot_data["conn"]
    now = _now(cfg)
    try:
        events = await llm.extract(cfg, text, now, db.kv_get(conn, "last_prompt"))
    except Exception as exc:
        log.exception("extraction failed")
        await update.message.reply_text(f"Couldn't parse that ({type(exc).__name__}). Nothing was saved; try again.")
        return
    if not events:
        await update.message.reply_text("I didn't find anything to log in that.")
        return
    db.insert_events(conn, events, now.astimezone(timezone.utc).isoformat(), text, source)
    lines = [f"• {e['metric_name']} = {e['metric_value']:g}"
             + (f" @ {e['event_time']}" if e["event_time"] else "")
             + (" (yesterday)" if e["log_date"] != now.date().isoformat() else "")
             for e in events]
    await update.message.reply_text("Logged:\n" + "\n".join(lines) + "\n/undo to remove.")


@owner_only
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _process(update, ctx, update.message.text, "text")


@owner_only
async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cfg = ctx.bot_data["cfg"]
    try:
        tg_file = await (update.message.voice or update.message.audio).get_file()
        text = await transcribe(cfg, bytes(await tg_file.download_as_bytearray()))
    except Exception as exc:
        log.exception("transcription failed")
        await update.message.reply_text(f"Couldn't transcribe that ({type(exc).__name__}: {exc}).")
        return
    await update.message.reply_text(f"Heard: “{text}”")
    await _process(update, ctx, text, "voice")


# ---------- wiring ----------

def build_app(cfg) -> Application:
    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["conn"] = db.connect(cfg.db_path)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if cfg.owner_id is not None:
        app.job_queue.run_daily(morning_job, cfg.morning_time.replace(tzinfo=cfg.tz), name="morning")
        app.job_queue.run_daily(evening_job, cfg.evening_time.replace(tzinfo=cfg.tz), name="evening")
    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = config_mod.load()
    if not cfg.telegram_token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (create a bot with @BotFather).")
    if cfg.owner_id is None:
        log.warning("TELEGRAM_OWNER_ID not set: bot will only reply to /start with your user id.")
    build_app(cfg).run_polling()
