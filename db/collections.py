"""Collection accessors and document shape helpers. Use get_db()[name] for raw access."""
import time
from pymongo.collection import Collection
from pymongo.database import Database

from db.client import get_db


def _coll(db: Database, name: str) -> Collection:
    return db[name]


def user_config_collection() -> Collection:
    return _coll(get_db(), "user_config")


def contests_collection() -> Collection:
    return _coll(get_db(), "contests")


def contest_registrations_collection() -> Collection:
    return _coll(get_db(), "contest_registrations")


def contest_results_collection() -> Collection:
    return _coll(get_db(), "contest_results")


def practice_solves_collection() -> Collection:
    return _coll(get_db(), "practice_solves")


def rating_history_collection() -> Collection:
    return _coll(get_db(), "rating_history")


def notification_log_collection() -> Collection:
    return _coll(get_db(), "notification_log")


def analytics_cache_collection() -> Collection:
    return _coll(get_db(), "analytics_cache")


# --- Document helpers (for consistent keys) ---
USER_ID_DEFAULT = "default"


def contest_doc(
    platform: str,
    external_id: str,
    name: str,
    start_time_utc: int,
    duration_seconds: int,
    phase: str = "BEFORE",
    rated: bool = True,
    division_or_type: str | None = None,
) -> dict:
    return {
        "platform": platform,
        "external_id": str(external_id),
        "name": name,
        "start_time_utc": start_time_utc,
        "duration_seconds": duration_seconds,
        "phase": phase,
        "rated": rated,
        "division_or_type": division_or_type or "",
    }


def practice_solve_doc(
    platform: str,
    user_id: str,
    problem_id: str,
    name: str,
    difficulty: str,
    tags: list[str],
    solved_at: int,
    time_seconds: int | None = None,
    submission_id: str | None = None,
) -> dict:
    return {
        "platform": platform,
        "user_id": user_id,
        "problem_id": problem_id,
        "name": name,
        "difficulty": difficulty,
        "tags": tags,
        "solved_at": solved_at,
        "time_seconds": time_seconds,
        "submission_id": submission_id,
    }


def rating_history_doc(
    platform: str,
    user_id: str,
    contest_id: str,
    old_rating: int,
    new_rating: int,
    timestamp: int,
) -> dict:
    return {
        "platform": platform,
        "user_id": user_id,
        "contest_id": contest_id,
        "old_rating": old_rating,
        "new_rating": new_rating,
        "timestamp": timestamp,
    }


def notification_log_doc(event_type: str, payload: dict | None = None, ref: str | None = None) -> dict:
    return {
        "event_type": event_type,
        "payload": payload or {},
        "ref": ref or "",
        "sent_at": int(time.time()),
    }
