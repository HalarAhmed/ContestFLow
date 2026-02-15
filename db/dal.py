"""Data access layer: get/update user_config, contests, registrations, practice_solves, etc."""
import time
from typing import Any

from bson import ObjectId

from db.collections import (
    USER_ID_DEFAULT,
    analytics_cache_collection,
    contest_doc,
    contest_registrations_collection,
    contest_results_collection,
    contests_collection,
    notification_log_collection,
    notification_log_doc,
    practice_solves_collection,
    practice_solve_doc,
    rating_history_collection,
    rating_history_doc,
    user_config_collection,
)


def _serialize_doc(doc: dict | None) -> dict | None:
    """Convert MongoDB ObjectId fields to strings so FastAPI can JSON-serialize them."""
    if doc is None:
        return None
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    return out


def _serialize_docs(docs: list[dict]) -> list[dict]:
    """Convert a list of MongoDB documents for JSON serialization."""
    return [_serialize_doc(d) for d in docs]


def get_user_config(user_id: str = USER_ID_DEFAULT) -> dict | None:
    doc = user_config_collection().find_one({"user_id": user_id})
    return _serialize_doc(doc)


def upsert_user_config(
    user_id: str,
    *,
    codeforces_handle: str | None = None,
    leetcode_username: str | None = None,
    timezone: str | None = None,
    target_rating: int | None = None,
    target_practice_hours_per_week: int | None = None,
    notification_email: str | None = None,
    reminders: list[str] | None = None,
) -> None:
    coll = user_config_collection()
    update: dict[str, Any] = {}
    if codeforces_handle is not None:
        update["codeforces_handle"] = codeforces_handle
    if leetcode_username is not None:
        update["leetcode_username"] = leetcode_username
    if timezone is not None:
        update["timezone"] = timezone
    if target_rating is not None:
        update["target_rating"] = target_rating
    if target_practice_hours_per_week is not None:
        update["target_practice_hours_per_week"] = target_practice_hours_per_week
    if notification_email is not None:
        if "notification" not in update:
            update["notification"] = {}
        doc = coll.find_one({"user_id": user_id}) or {}
        notif = doc.get("notification") or {}
        notif["email"] = notification_email
        update["notification"] = notif
    if reminders is not None:
        if "notification" not in update:
            doc = coll.find_one({"user_id": user_id}) or {}
            update["notification"] = doc.get("notification") or {}
        update["notification"]["reminders"] = reminders
    if not update:
        return
    coll.update_one(
        {"user_id": user_id},
        {"$set": update},
        upsert=True,
    )


def get_or_create_user_config(user_id: str = USER_ID_DEFAULT) -> dict:
    doc = get_user_config(user_id)
    if doc:
        return doc
    from config import settings
    default = {
        "user_id": user_id,
        "codeforces_handle": settings.CODEFORCES_HANDLE,
        "leetcode_username": settings.LEETCODE_USERNAME,
        "timezone": settings.USER_TIMEZONE,
        "target_rating": 1600,
        "target_practice_hours_per_week": 10,
        "notification": {"email": settings.NOTIFICATION_EMAIL or "", "reminders": ["new_contest", "24h", "1h", "15m"]},
    }
    r = user_config_collection().insert_one(default)
    default["_id"] = str(r.inserted_id)
    return default


def get_browser_cookies(user_id: str, platform: str) -> list[dict] | None:
    """Return stored browser cookies for the platform, or None if not set."""
    doc = user_config_collection().find_one({"user_id": user_id})
    if not doc:
        return None
    cookies_map = doc.get("browser_cookies") or {}
    out = cookies_map.get(platform)
    return out if isinstance(out, list) and len(out) > 0 else None


def set_browser_cookies(user_id: str, platform: str, cookies: list[dict]) -> None:
    """Store browser cookies for the platform (from pasted Netscape/JSON)."""
    coll = user_config_collection()
    doc = coll.find_one({"user_id": user_id}) or {}
    existing = doc.get("browser_cookies") or {}
    existing[platform] = cookies
    coll.update_one(
        {"user_id": user_id},
        {"$set": {"browser_cookies": existing}},
        upsert=True,
    )


def set_user_passwords(user_id: str, codeforces_password: str = "", leetcode_password: str = "") -> None:
    """Store platform passwords in user_config (for auto-registration)."""
    update: dict[str, str] = {}
    if codeforces_password:
        update["codeforces_password"] = codeforces_password
    if leetcode_password:
        update["leetcode_password"] = leetcode_password
    if update:
        user_config_collection().update_one(
            {"user_id": user_id}, {"$set": update}, upsert=True,
        )


def get_user_passwords(user_id: str) -> dict[str, str]:
    """Return stored passwords {'codeforces_password': ..., 'leetcode_password': ...}."""
    doc = user_config_collection().find_one({"user_id": user_id}) or {}
    return {
        "codeforces_password": doc.get("codeforces_password", ""),
        "leetcode_password": doc.get("leetcode_password", ""),
    }


def get_registrations(user_id: str) -> list[dict]:
    """Return all contest registrations for the user, enriched with contest info."""
    regs = list(contest_registrations_collection().find({"user_id": user_id}).sort("created_at", -1))
    result = []
    for r in regs:
        reg = _serialize_doc(r)
        # Try to enrich with contest name/time from contests collection
        contest = contests_collection().find_one({"external_id": reg.get("contest_id")})
        if contest:
            reg["contest_name"] = contest.get("name", "")
            reg["platform"] = contest.get("platform", "")
            reg["start_time_utc"] = contest.get("start_time_utc", 0)
        else:
            reg.setdefault("contest_name", reg.get("contest_id", ""))
            reg.setdefault("platform", reg.get("platform", ""))
            reg.setdefault("start_time_utc", 0)
        result.append(reg)
    return result


# --- Contests ---
def upsert_contest(
    platform: str,
    external_id: str,
    name: str,
    start_time_utc: int,
    duration_seconds: int,
    phase: str = "BEFORE",
    rated: bool = True,
    division_or_type: str | None = None,
) -> bool:
    """Insert contest if not exists. Returns True if newly inserted."""
    doc = contest_doc(
        platform=platform,
        external_id=external_id,
        name=name,
        start_time_utc=start_time_utc,
        duration_seconds=duration_seconds,
        phase=phase,
        rated=rated,
        division_or_type=division_or_type,
    )
    r = contests_collection().update_one(
        {"platform": platform, "external_id": str(external_id)},
        {"$set": doc},
        upsert=True,
    )
    return r.upserted_id is not None


def get_upcoming_contests(platform: str | None = None) -> list[dict]:
    now = int(time.time())
    q = {"start_time_utc": {"$gt": now}, "phase": "BEFORE"}
    if platform:
        q["platform"] = platform
    cursor = contests_collection().find(q).sort("start_time_utc", 1)
    return _serialize_docs(list(cursor))


def get_contest_by_id(contest_id: str) -> dict | None:
    if isinstance(contest_id, ObjectId):
        return _serialize_doc(contests_collection().find_one({"_id": contest_id}))
    try:
        oid = ObjectId(contest_id)
        doc = contests_collection().find_one({"_id": oid})
        if doc:
            return _serialize_doc(doc)
    except Exception:
        pass
    return _serialize_doc(contests_collection().find_one({"platform": {"$exists": True}, "external_id": str(contest_id)}))


def get_contest_by_platform_and_external(platform: str, external_id: str) -> dict | None:
    return _serialize_doc(contests_collection().find_one({"platform": platform, "external_id": str(external_id)}))


# --- Contest registrations ---
def add_registration(user_id: str, contest_id: str, status: str = "pending", error_message: str | None = None) -> str:
    doc = {
        "user_id": user_id,
        "contest_id": contest_id,
        "status": status,
        "error_message": error_message or "",
        "created_at": int(time.time()),
    }
    r = contest_registrations_collection().insert_one(doc)
    return str(r.inserted_id)


def update_registration_status(registration_id: str | None, contest_id: str, user_id: str, status: str, error_message: str = "") -> None:
    if registration_id:
        try:
            contest_registrations_collection().update_one(
                {"_id": ObjectId(registration_id)},
                {"$set": {"status": status, "error_message": error_message}},
            )
            return
        except Exception:
            pass
    contest_registrations_collection().update_one(
        {"user_id": user_id, "contest_id": contest_id},
        {"$set": {"status": status, "error_message": error_message}},
        upsert=True,
    )


# --- Contest results ---
def save_contest_result(
    contest_id: str,
    user_id: str,
    rank: int,
    old_rating: int,
    new_rating: int,
    problems_solved: int,
    penalty: int,
    problem_details: list[dict] | None = None,
) -> None:
    doc = {
        "contest_id": contest_id,
        "user_id": user_id,
        "rank": rank,
        "old_rating": old_rating,
        "new_rating": new_rating,
        "problems_solved": problems_solved,
        "penalty": penalty,
        "problem_details": problem_details or [],
    }
    contest_results_collection().update_one(
        {"contest_id": contest_id, "user_id": user_id},
        {"$set": doc},
        upsert=True,
    )


# --- Practice solves ---
def upsert_practice_solve(
    platform: str,
    user_id: str,
    problem_id: str,
    name: str,
    difficulty: str,
    tags: list[str],
    solved_at: int,
    time_seconds: int | None = None,
    submission_id: str | None = None,
) -> None:
    doc = practice_solve_doc(
        platform=platform,
        user_id=user_id,
        problem_id=problem_id,
        name=name,
        difficulty=difficulty,
        tags=tags,
        solved_at=solved_at,
        time_seconds=time_seconds,
        submission_id=submission_id,
    )
    practice_solves_collection().update_one(
        {"platform": platform, "user_id": user_id, "problem_id": problem_id},
        {"$set": doc},
        upsert=True,
    )


def get_practice_solves(user_id: str, from_ts: int | None = None, to_ts: int | None = None, platform: str | None = None) -> list[dict]:
    q: dict = {"user_id": user_id}
    if from_ts is not None or to_ts is not None:
        q["solved_at"] = {}
        if from_ts is not None:
            q["solved_at"]["$gte"] = from_ts
        if to_ts is not None:
            q["solved_at"]["$lte"] = to_ts
    if platform:
        q["platform"] = platform
    return _serialize_docs(list(practice_solves_collection().find(q).sort("solved_at", -1)))


# --- Rating history ---
def add_rating_change(platform: str, user_id: str, contest_id: str, old_rating: int, new_rating: int, timestamp: int) -> None:
    doc = rating_history_doc(platform=platform, user_id=user_id, contest_id=contest_id, old_rating=old_rating, new_rating=new_rating, timestamp=timestamp)
    rating_history_collection().update_one(
        {"platform": platform, "user_id": user_id, "contest_id": contest_id},
        {"$set": doc},
        upsert=True,
    )


def get_rating_history(user_id: str, platform: str | None = None, limit: int = 100) -> list[dict]:
    q = {"user_id": user_id}
    if platform:
        q["platform"] = platform
    return _serialize_docs(list(rating_history_collection().find(q).sort("timestamp", -1).limit(limit)))


# --- Notification log ---
def log_notification_sent(event_type: str, payload: dict | None = None, ref: str | None = None) -> None:
    notification_log_collection().insert_one(notification_log_doc(event_type=event_type, payload=payload or {}, ref=ref or ""))


def was_notification_sent(event_type: str, ref: str, within_seconds: int = 86400) -> bool:
    since = int(time.time()) - within_seconds
    return notification_log_collection().find_one({"event_type": event_type, "ref": ref, "sent_at": {"$gte": since}}) is not None


# --- Analytics cache ---
def get_analytics_cache(user_id: str = USER_ID_DEFAULT) -> dict | None:
    return _serialize_doc(analytics_cache_collection().find_one({"user_id": user_id}))


def set_analytics_cache(user_id: str, data: dict) -> None:
    data["user_id"] = user_id
    data["last_updated"] = int(time.time())
    analytics_cache_collection().update_one({"user_id": user_id}, {"$set": data}, upsert=True)
