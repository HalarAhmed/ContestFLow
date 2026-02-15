"""LangChain tools for contest check, registration, practice summary, notifications."""
from typing import Optional

from langchain_core.tools import tool
from config import settings

from db.dal import (
    USER_ID_DEFAULT,
    get_or_create_user_config,
    get_practice_solves,
)
from integrations.codeforces import CodeforcesAPI, get_upcoming_cf_contests
from integrations.leetcode import LeetCodeAPI, get_upcoming_lc_contests
from integrations.notifications import send_email
from analytics.recommendations import get_weak_strong_tags, get_recommended_practice_plan
from automation.register_cf import register_codeforces
from automation.register_leetcode import register_leetcode
from utils.logging import get_logger

logger = get_logger(__name__)


@tool
def get_upcoming_contests_tool(platform: Optional[str] = None) -> str:
    """Get upcoming contests from Codeforces and/or LeetCode. platform: 'codeforces', 'leetcode', or None for both."""
    try:
        if platform == "leetcode" or not platform:
            lc = get_upcoming_lc_contests()
            lc_str = "\n".join([f"LeetCode: {c.get('title')} (start: {c.get('startTime')})" for c in lc[:5]]) if lc else "No upcoming LeetCode contests."
        else:
            lc_str = ""
        if platform == "codeforces" or not platform:
            cf = get_upcoming_cf_contests()
            cf_str = "\n".join([f"Codeforces: {c.get('name')} (id={c.get('id')}, start: {c.get('startTimeSeconds')})" for c in cf[:10]]) if cf else "No upcoming Codeforces contests."
        else:
            cf_str = ""
        if platform == "leetcode":
            return lc_str or "No upcoming contests."
        if platform == "codeforces":
            return cf_str or "No upcoming contests."
        return (cf_str + "\n" + lc_str) if cf_str and lc_str else (cf_str or lc_str or "No upcoming contests.")
    except Exception as e:
        return f"Error fetching contests: {e}"


@tool
def get_user_rating_tool(platform: str) -> str:
    """Get current user rating for a platform. platform: 'codeforces' or 'leetcode'."""
    config = get_or_create_user_config(USER_ID_DEFAULT)
    if platform == "codeforces":
        handle = config.get("codeforces_handle") or settings.CODEFORCES_HANDLE
        if not handle:
            return "Codeforces handle not set."
        try:
            info = CodeforcesAPI.user_info(handle)
            if info:
                u = info[0]
                return f"Rating: {u.get('rating', 'N/A')}, Max: {u.get('maxRating', 'N/A')}"
            rating = CodeforcesAPI.user_rating(handle)
            if rating:
                return f"Rating: {rating[-1].get('newRating', 'N/A')}"
            return "No rating data."
        except Exception as e:
            return f"Error: {e}"
    if platform == "leetcode":
        username = config.get("leetcode_username") or settings.LEETCODE_USERNAME
        if not username:
            return "LeetCode username not set."
        try:
            profile = LeetCodeAPI.profile(username)
            return f"Solved: {profile.get('totalSolved', 'N/A')}, Easy: {profile.get('easySolved')}, Medium: {profile.get('mediumSolved')}, Hard: {profile.get('hardSolved')}"
        except Exception as e:
            return f"Error: {e}"
    return "Platform must be 'codeforces' or 'leetcode'."


@tool
def register_for_contest_tool(platform: str, contest_identifier: str) -> str:
    """Register the user for a contest. platform: 'codeforces' or 'leetcode'. contest_identifier: contest id (CF) or slug (LC)."""
    config = get_or_create_user_config(USER_ID_DEFAULT)
    if platform == "codeforces":
        ok, msg = register_codeforces(
            contest_id=contest_identifier,
            username=config.get("codeforces_handle") or settings.CODEFORCES_HANDLE,
            password=settings.CODEFORCES_PASSWORD,
        )
        return f"Codeforces registration: {'Success' if ok else 'Failed'} - {msg}"
    if platform == "leetcode":
        ok, msg = register_leetcode(
            contest_slug=contest_identifier,
            username=config.get("leetcode_username") or settings.LEETCODE_USERNAME,
            password=settings.LEETCODE_PASSWORD,
        )
        return f"LeetCode registration: {'Success' if ok else 'Failed'} - {msg}"
    return "Platform must be 'codeforces' or 'leetcode'."


@tool
def get_practice_summary_tool(days: int = 7) -> str:
    """Get practice summary for the last N days (problems solved, by platform)."""
    import time
    to_ts = int(time.time())
    from_ts = to_ts - days * 86400
    solves = get_practice_solves(user_id=USER_ID_DEFAULT, from_ts=from_ts, to_ts=to_ts)
    by_platform = {}
    for s in solves:
        p = s.get("platform", "unknown")
        by_platform[p] = by_platform.get(p, 0) + 1
    return f"Last {days} days: {len(solves)} problems solved. By platform: {by_platform}"


@tool
def get_weak_strong_tags_tool() -> str:
    """Get user's weakest and strongest tags based on practice history."""
    data = get_weak_strong_tags(USER_ID_DEFAULT, use_cache=False)
    return f"Weak tags: {data.get('weak_tags', [])}. Strong tags: {data.get('strong_tags', [])}. Total solved: {data.get('total_solved', 0)}"


@tool
def get_training_plan_tool() -> str:
    """Get recommended training plan for today (problems by tag and difficulty)."""
    plan = get_recommended_practice_plan(USER_ID_DEFAULT)
    lines = ["Training plan:", *plan.get("problems_today", [])]
    return "\n".join(lines)


@tool
def send_notification_tool(message: str, subject: str = "CP Assistant") -> str:
    """Send an email notification to the configured user."""
    config = get_or_create_user_config(USER_ID_DEFAULT)
    email = (config.get("notification") or {}).get("email") or settings.NOTIFICATION_EMAIL
    if not email:
        return "No notification email configured."
    ok = send_email(email, subject, message)
    return "Email sent." if ok else "Failed to send email."


def get_tools() -> list:
    return [
        get_upcoming_contests_tool,
        get_user_rating_tool,
        register_for_contest_tool,
        get_practice_summary_tool,
        get_weak_strong_tags_tool,
        get_training_plan_tool,
        send_notification_tool,
    ]
