"""LeetCode client using alfa-leetcode-api (https://alfa-leetcode-api.onrender.com/)."""
import time
from typing import Any

import requests
from utils.logging import get_logger

LC_BASE = "https://alfa-leetcode-api.onrender.com"

logger = get_logger(__name__)

# Simple cache to avoid 429 (API is rate-limited). TTL 5 min.
_lc_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300


def _get(path: str, params: dict | None = None) -> Any:
    cache_key = path + ("?" + str(sorted((params or {}).items())) if params else "")
    now = time.time()
    if cache_key in _lc_cache:
        ts, data = _lc_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return data
    url = f"{LC_BASE}{path}"
    try:
        r = requests.get(url, params=params or {}, timeout=30)
        if r.status_code == 429:
            logger.warning("LeetCode API rate limited (429). Using cached or empty data. Try again in a few minutes.")
            empty = {} if "/contest" not in path else {"contests": []}
            _lc_cache[cache_key] = (now, empty)
            return empty
        r.raise_for_status()
        data = r.json()
        _lc_cache[cache_key] = (now, data)
        return data
    except requests.HTTPError as e:
        if e.response.status_code == 429:
            logger.warning("LeetCode API rate limited (429). Try again in a few minutes.")
            return {}
        logger.warning("LeetCode API %s failed: %s", path, e)
        raise
    except Exception as e:
        logger.warning("LeetCode API %s failed: %s", path, e)
        raise


class LeetCodeAPI:
    @staticmethod
    def profile(username: str) -> dict:
        return _get(f"/{username}/profile")

    @staticmethod
    def submissions(username: str, limit: int = 20) -> list[dict]:
        return _get(f"/{username}/submission", {"limit": limit})

    @staticmethod
    def ac_submissions(username: str, limit: int = 100) -> list[dict]:
        return _get(f"/{username}/acSubmission", {"limit": limit})

    @staticmethod
    def contest_history(username: str) -> list[dict]:
        return _get(f"/{username}/contest/history")

    @staticmethod
    def contests_upcoming() -> list[dict]:
        data = _get("/contests/upcoming")
        if not isinstance(data, dict):
            return data if isinstance(data, list) else []
        out = data.get("contests", data)
        return out if isinstance(out, list) else []

    @staticmethod
    def calendar(username: str, year: int | None = None) -> dict:
        path = f"/{username}/calendar"
        if year:
            return _get(path, {"year": year})
        return _get(path)


def get_upcoming_lc_contests() -> list[dict]:
    """Return upcoming contests from LeetCode API."""
    raw = LeetCodeAPI.contests_upcoming()
    if isinstance(raw, list):
        return raw
    return []
