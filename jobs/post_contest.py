"""After a contest ends: fetch standings and rating changes, save report, optionally email."""
import time
from typing import Any

from db import contests_collection
from db.dal import (
    add_rating_change,
    get_or_create_user_config,
    save_contest_result,
    log_notification_sent,
    was_notification_sent,
)
from integrations.codeforces import CodeforcesAPI
from integrations.leetcode import LeetCodeAPI
from integrations.notifications import notify_post_contest_report
from utils.logging import get_logger

logger = get_logger(__name__)


def _analyze_cf_contest(contest_id_str: str, handle: str, user_id: str) -> str | None:
    try:
        cid = int(contest_id_str)
        rating_changes = CodeforcesAPI.contest_rating_changes(cid)
        my_change = next((rc for rc in rating_changes if rc.get("handle") == handle), None)
        if not my_change:
            return None
        standings = CodeforcesAPI.contest_standings(cid, handles=handle, count=1)
        rows = standings.get("rows") or []
        problems = standings.get("problems") or []
        row = rows[0] if rows else None
        if not row:
            return None
        problem_details = []
        for i, p in enumerate(problems or []):
            idx = p.get("index", str(i))
            try:
                pt = next((x for x in (row.get("problemResults") or []) if x.get("index") == idx or x.get("index") == i), {})
                problem_details.append({
                    "index": idx,
                    "solved": pt.get("points", 0) > 0,
                    "time_seconds": int(pt.get("bestSubmissionTimeSeconds", 0) or 0),
                    "attempts": int(pt.get("rejectedAttemptCount", 0) or 0),
                })
            except Exception:
                pass
        old_r = int(my_change.get("oldRating", 0))
        new_r = int(my_change.get("newRating", 0))
        save_contest_result(
            contest_id=contest_id_str,
            user_id=user_id,
            rank=int(row.get("rank", 0)),
            old_rating=old_r,
            new_rating=new_r,
            problems_solved=int(row.get("solvedCount", 0)),
            penalty=int(row.get("penalty", 0)),
            problem_details=problem_details,
        )
        add_rating_change("codeforces", user_id, contest_id_str, old_r, new_r, int(time.time()))
        add_rating_change_str = (
            f"Rating: {old_r} -> {new_r} "
            f"({'+' if new_r >= old_r else ''}{new_r - old_r})"
        )
        return (
            f"Contest: {standings.get('contest', {}).get('name', contest_id_str)}\n"
            f"Rank: {row.get('rank')}\n"
            f"Problems solved: {row.get('solvedCount')}\n"
            f"{add_rating_change_str}\n"
            f"Penalty: {row.get('penalty')}"
        )
    except Exception as e:
        logger.exception("CF post-contest analysis failed: %s", e)
        return None


def _analyze_lc_contest(contest_slug: str, username: str, user_id: str) -> str | None:
    try:
        history = LeetCodeAPI.contest_history(username)
        if not isinstance(history, list):
            return None
        for h in history:
            if (h.get("titleSlug") or "").lower() == contest_slug.lower():
                summary = (
                    f"Contest: {h.get('title', contest_slug)}\n"
                    f"Rank: {h.get('ranking', 'N/A')}\n"
                    f"Score: {h.get('score', 'N/A')}\n"
                    f"Rating: {h.get('rating', 'N/A')}"
                )
                save_contest_result(
                    contest_id=contest_slug,
                    user_id=user_id,
                    rank=int(h.get("ranking", 0)) or 0,
                    old_rating=0,
                    new_rating=int(h.get("rating", 0)) or 0,
                    problems_solved=0,
                    penalty=0,
                    problem_details=[],
                )
                return summary
        return None
    except Exception as e:
        logger.exception("LC post-contest analysis failed: %s", e)
        return None


def run_post_contest_analysis(user_id: str = "default") -> None:
    config = get_or_create_user_config(user_id)
    email = (config.get("notification") or {}).get("email") or ""
    cf_handle = config.get("codeforces_handle") or ""
    lc_username = config.get("leetcode_username") or ""
    now = int(time.time())

    # Find contests that have ended recently (phase FINISHED or end time in the past)
    for doc in contests_collection().find({"start_time_utc": {"$lt": now}}):
        platform = doc.get("platform", "")
        ext_id = doc.get("external_id", "")
        name = doc.get("name", "")
        start = doc.get("start_time_utc", 0)
        duration = doc.get("duration_seconds", 0)
        end_time = start + duration
        if end_time > now - 3600:
            continue
        ref = f"post_{platform}_{ext_id}"
        if was_notification_sent("post_contest", ref, within_seconds=7 * 86400):
            continue
        summary = None
        if platform == "codeforces" and cf_handle:
            summary = _analyze_cf_contest(ext_id, cf_handle, user_id)
        elif platform == "leetcode" and lc_username:
            summary = _analyze_lc_contest(ext_id, lc_username, user_id)
        if summary and email:
            if notify_post_contest_report(name, summary, email):
                log_notification_sent("post_contest", {"contest": name}, ref)
