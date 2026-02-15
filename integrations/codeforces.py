"""Codeforces official API client. Rate limit: 1 request per 2 seconds."""
import time
from typing import Any

import requests
from utils.logging import get_logger

CF_BASE = "https://codeforces.com/api"

logger = get_logger(__name__)

_last_request_time = 0.0
MIN_INTERVAL = 2.0


def _rate_limit() -> None:
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get(method: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
    _rate_limit()
    url = f"{CF_BASE}/{method}"
    try:
        r = requests.get(url, params=params or {}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "OK":
            raise RuntimeError(data.get("comment", "Unknown error"))
        return data.get("result", data)
    except Exception as e:
        logger.warning("Codeforces API %s failed: %s", method, e)
        raise


class CodeforcesAPI:
    @staticmethod
    def contest_list(gym: bool = False) -> list[dict]:
        return _get("contest.list", {"gym": "true" if gym else "false"})

    @staticmethod
    def user_rating(handle: str) -> list[dict]:
        return _get("user.rating", {"handle": handle})

    @staticmethod
    def user_info(handles: str | list[str]) -> list[dict]:
        if isinstance(handles, list):
            handles = ";".join(handles)
        return _get("user.info", {"handles": handles})

    @staticmethod
    def contest_standings(contest_id: int, handles: str | None = None, count: int = 100) -> dict:
        params: dict = {"contestId": contest_id, "from": 1, "count": count, "showUnofficial": "false"}
        if handles:
            params["handles"] = handles
        return _get("contest.standings", params)

    @staticmethod
    def contest_rating_changes(contest_id: int) -> list[dict]:
        return _get("contest.ratingChanges", {"contestId": contest_id})

    @staticmethod
    def user_status(handle: str, from_index: int = 1, count: int = 100) -> list[dict]:
        return _get("user.status", {"handle": handle, "from": from_index, "count": count})

    @staticmethod
    def problemset_problems(tags: str | None = None) -> dict:
        params = {}
        if tags:
            params["tags"] = tags
        return _get("problemset.problems", params if params else None)


def get_upcoming_cf_contests() -> list[dict]:
    """Return contests with phase BEFORE and start_time in the future."""
    now = int(time.time())
    all_contests = CodeforcesAPI.contest_list(gym=False)
    upcoming = [c for c in all_contests if c.get("phase") == "BEFORE" and c.get("startTimeSeconds", 0) > now]
    return sorted(upcoming, key=lambda x: x["startTimeSeconds"])
