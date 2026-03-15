import os
import random
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from notifier import send_telegram_alert
from scraper import NYUMealScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("meal-api")


class Config:
    threshold: int = int(os.getenv("SWIPE_THRESHOLD", 3))
    poll_minutes: int = int(os.getenv("POLL_INTERVAL_MINUTES", 2))
    use_mock: bool = os.getenv("USE_MOCK", "false").lower() == "true"


config = Config()


class AppState:
    swipe_count: int | None = None
    previous_swipe_count: int | None = None
    last_check: str | None = None
    last_alert_sent: str | None = None
    error: str | None = None
    checks_total: int = 0
    alerts_total: int = 0
    session_start: datetime | None = None
    session_active: bool = False
    last_session_duration: str | None = None
    successful_checks_this_session: int = 0


state = AppState()


def format_duration(td) -> str:
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"


async def fetch_swipe_count() -> dict:
    if config.use_mock:
        return {"swipe_count": random.randint(0, 8), "authenticated": True, "error": None}

    scraper = NYUMealScraper()
    result = scraper.get_swipe_count()
    return result


async def check_and_alert() -> dict:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    state.checks_total += 1

    try:
        result = await fetch_swipe_count()

        # Session expired
        if not result["authenticated"]:
            duration_str = None
            if state.session_active and state.session_start:
                duration = now - state.session_start
                duration_str = format_duration(duration)
                state.last_session_duration = duration_str
                logger.warning(f"Session expired after {duration_str} ({state.successful_checks_this_session} successful checks)")

            state.session_active = False
            state.last_check = now_iso
            state.error = result.get("error")

            await send_telegram_alert(
                "<b>⚠️ SESSION EXPIRED</b>\n\n"
                f"Session lasted: <b>{duration_str or 'unknown'}</b>\n"
                f"Successful checks: {state.successful_checks_this_session}\n"
                f"Time: {now.strftime('%I:%M %p UTC')}\n"
                "Manual login required to resume."
            )
            state.alerts_total += 1

            return {
                "swipe_count": None,
                "last_check": now_iso,
                "alert_sent": True,
                "session_expired": True,
                "session_duration": duration_str,
            }

        if result["error"]:
            raise Exception(result["error"])

        count = result["swipe_count"]
        state.swipe_count = count
        state.last_check = now_iso
        state.error = None

        # Start session tracking on first successful auth
        if not state.session_active:
            state.session_start = now
            state.session_active = True
            state.successful_checks_this_session = 0
            logger.info("New session started")

        state.successful_checks_this_session += 1
        session_duration = format_duration(now - state.session_start)

        logger.info(f"Swipe count: {count} | Session uptime: {session_duration}")

        # Only notify on change or low threshold
        alert_sent = False
        prev = state.previous_swipe_count

        if count is not None and count != prev:
            if count <= config.threshold:
                msg = (
                    "<b>🚨 LOW SWIPE ALERT 🚨</b>\n\n"
                    f"Only <b>{count}</b> meal swipes remaining!\n"
                    f"(Threshold: {config.threshold})"
                )
            else:
                diff = count - prev if prev is not None else 0
                direction = f"🔻 {abs(diff)}" if diff < 0 else f"🔺 +{diff}"
                msg = (
                    f"<b>🍽 Swipe Update</b>\n\n"
                    f"Balance: <b>{count}</b> swipes ({direction})\n"
                    f"Session uptime: {session_duration}\n"
                    f"Checked at: {now.strftime('%I:%M %p UTC')}"
                )

            await send_telegram_alert(msg)
            state.last_alert_sent = now_iso
            state.alerts_total += 1
            alert_sent = True

        state.previous_swipe_count = count

        return {
            "swipe_count": count,
            "last_check": now_iso,
            "alert_sent": alert_sent,
            "session_uptime": session_duration,
        }

    except Exception as e:
        state.error = str(e)
        state.last_check = now_iso
        logger.error(f"Error checking swipes: {e}")
        return {"swipe_count": None, "last_check": now_iso, "alert_sent": False, "error": str(e)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_and_alert,
        "interval",
        minutes=config.poll_minutes,
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    logger.info(f"Scheduler started (polling every {config.poll_minutes}m, mock={config.use_mock})")
    yield
    scheduler.shutdown()


app = FastAPI(title="Meal Swipe Bot", lifespan=lifespan)


@app.get("/")
async def get_status():
    session_uptime = None
    if state.session_active and state.session_start:
        session_uptime = format_duration(datetime.now(timezone.utc) - state.session_start)

    return {
        "status": "online",
        "current_state": {
            "swipe_count": state.swipe_count,
            "last_check": state.last_check,
            "error": state.error,
        },
        "session": {
            "active": state.session_active,
            "started": state.session_start.isoformat() if state.session_start else None,
            "uptime": session_uptime,
            "successful_checks": state.successful_checks_this_session,
            "last_session_duration": state.last_session_duration,
        },
        "stats": {
            "checks_total": state.checks_total,
            "alerts_total": state.alerts_total,
        },
        "config": {
            "threshold": config.threshold,
            "poll_interval": config.poll_minutes,
            "use_mock": config.use_mock,
        },
    }


@app.post("/trigger")
async def trigger_check():
    result = await check_and_alert()
    return result


@app.post("/test-alert")
async def test_alert():
    sent = await send_telegram_alert(
        "✅ <b>Test Alert</b>\nYour NYU Meal Swipe Bot is connected!"
    )
    if not sent:
        raise HTTPException(500, "Failed to send — check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")
    return {"message": "Test alert sent successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
