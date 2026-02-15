"""Fetch upcoming contests, upsert DB, send new-contest and reminder emails (24h, 1h, 15m)."""
import time
from datetime import datetime

import pytz
from db import contests_collection
from db.dal import (
    get_or_create_user_config,
    log_notification_sent,
    upsert_contest,
    was_notification_sent,
)
from integrations.codeforces import get_upcoming_cf_contests
from integrations.leetcode import get_upcoming_lc_contests
from integrations.notifications import notify_contest_new, notify_contest_reminder
from utils.logging import get_logger

logger = get_logger(__name__)

REMINDER_WINDOWS = [
    (24 * 3600, "24 hours"),
    (3600, "1 hour"),
    (15 * 60, "15 minutes"),
]


def _to_local_iso(utc_ts: int, tz_name: str) -> str:
    try:
        tz = pytz.timezone(tz_name)
        dt = datetime.fromtimestamp(utc_ts, tz=pytz.UTC)
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return datetime.fromtimestamp(utc_ts, tz=pytz.UTC).strftime("%Y-%m-%d %H:%M UTC")


def _duration_str(seconds: int) -> str:
    h, s = divmod(seconds, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def run_contest_monitor(user_id: str = "default") -> None:
    config = get_or_create_user_config(user_id)
    email = (config.get("notification") or {}).get("email") or ""
    reminders_list = (config.get("notification") or {}).get("reminders") or ["new_contest", "24h", "1h", "15m"]
    tz_name = config.get("timezone") or "UTC"
    now = int(time.time())

    # Codeforces
    try:
        for c in get_upcoming_cf_contests():
            ext_id = str(c["id"])
            start_utc = c["startTimeSeconds"]
            duration = c.get("durationSeconds", 0)
            name = c.get("name", "Unknown")
            phase = c.get("phase", "BEFORE")
            is_new = upsert_contest(
                platform="codeforces",
                external_id=ext_id,
                name=name,
                start_time_utc=start_utc,
                duration_seconds=duration,
                phase=phase,
                rated="Rated" in name or "rated" in name.lower(),
                division_or_type=name,
            )
            ref = f"cf_{ext_id}"
            if is_new and "new_contest" in reminders_list and email:
                if not was_notification_sent("new_contest", ref):
                    start_str = _to_local_iso(start_utc, tz_name)
                    if notify_contest_new("Codeforces", name, start_str, _duration_str(duration), email):
                        log_notification_sent("new_contest", {"contest": name}, ref)
            # Reminders
            for delta_sec, label in REMINDER_WINDOWS:
                event = "24h" if delta_sec == 24 * 3600 else ("1h" if delta_sec == 3600 else "15m")
                if event not in reminders_list:
                    continue
                ref_r = f"cf_{ext_id}_{event}"
                if was_notification_sent(event, ref_r, within_seconds=delta_sec + 3600):
                    continue
                if start_utc - delta_sec <= now <= start_utc - delta_sec + 900:
                    start_str = _to_local_iso(start_utc, tz_name)
                    if notify_contest_reminder(name, label, start_str, email):
                        log_notification_sent(event, {"contest": name}, ref_r)
    except Exception as e:
        logger.exception("Codeforces contest monitor failed: %s", e)

    # LeetCode
    try:
        for c in get_upcoming_lc_contests():
            ext_id = c.get("titleSlug") or c.get("title", "").replace(" ", "-").lower()
            start_utc = c.get("startTime") or 0
            if start_utc <= now:
                continue
            duration = c.get("duration", 5400)
            name = c.get("title", "Unknown")
            is_new = upsert_contest(
                platform="leetcode",
                external_id=ext_id,
                name=name,
                start_time_utc=start_utc,
                duration_seconds=duration,
                phase="BEFORE",
                rated=True,
                division_or_type="",
            )
            ref = f"lc_{ext_id}"
            if is_new and "new_contest" in reminders_list and email:
                if not was_notification_sent("new_contest", ref):
                    start_str = _to_local_iso(start_utc, tz_name)
                    if notify_contest_new("LeetCode", name, start_str, _duration_str(duration), email):
                        log_notification_sent("new_contest", {"contest": name}, ref)
            for delta_sec, label in REMINDER_WINDOWS:
                event = "24h" if delta_sec == 24 * 3600 else ("1h" if delta_sec == 3600 else "15m")
                if event not in reminders_list:
                    continue
                ref_r = f"lc_{ext_id}_{event}"
                if was_notification_sent(event, ref_r, within_seconds=delta_sec + 3600):
                    continue
                if start_utc - delta_sec <= now <= start_utc - delta_sec + 900:
                    start_str = _to_local_iso(start_utc, tz_name)
                    if notify_contest_reminder(name, label, start_str, email):
                        log_notification_sent(event, {"contest": name}, ref_r)
    except Exception as e:
        logger.exception("LeetCode contest monitor failed: %s", e)
