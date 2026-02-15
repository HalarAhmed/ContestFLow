"""Sync practice solves and rating history from Codeforces and LeetCode into MongoDB."""
import time
from typing import Any

from db.dal import (
    USER_ID_DEFAULT,
    add_rating_change,
    get_or_create_user_config,
    upsert_practice_solve,
)
from integrations.codeforces import CodeforcesAPI
from integrations.leetcode import LeetCodeAPI
from utils.logging import get_logger

logger = get_logger(__name__)


def _sync_codeforces(handle: str, user_id: str) -> None:
    try:
        # Rating history
        for r in CodeforcesAPI.user_rating(handle):
            contest_id = str(r.get("contestId", ""))
            add_rating_change(
                platform="codeforces",
                user_id=user_id,
                contest_id=contest_id,
                old_rating=int(r.get("oldRating", 0)),
                new_rating=int(r.get("newRating", 0)),
                timestamp=int(r.get("ratingUpdateTimeSeconds", time.time())),
            )
        # Submissions (AC only for practice)
        subs = CodeforcesAPI.user_status(handle, from_index=1, count=200)
        for s in subs:
            if s.get("verdict") != "OK":
                continue
            prob = s.get("problem") or {}
            problem_id = f"{prob.get('contestId', '')}_{prob.get('index', '')}"
            name = prob.get("name") or problem_id
            tags = prob.get("tags") or []
            creation_time = int(s.get("creationTimeSeconds", time.time()))
            upsert_practice_solve(
                platform="codeforces",
                user_id=user_id,
                problem_id=problem_id,
                name=name,
                difficulty=str(prob.get("rating", "")),
                tags=tags,
                solved_at=creation_time,
                time_seconds=None,
                submission_id=str(s.get("id", "")),
            )
    except Exception as e:
        logger.exception("CF practice sync failed: %s", e)


def _sync_leetcode(username: str, user_id: str) -> None:
    try:
        ac_subs = LeetCodeAPI.ac_submissions(username, limit=200)
        for s in ac_subs if isinstance(ac_subs, list) else []:
            title = s.get("title") or ""
            title_slug = s.get("titleSlug") or title.lower().replace(" ", "-")
            difficulty = (s.get("difficulty") or "Unknown").capitalize()
            timestamp = int(s.get("timestamp", time.time()))
            upsert_practice_solve(
                platform="leetcode",
                user_id=user_id,
                problem_id=title_slug,
                name=title,
                difficulty=difficulty,
                tags=[],  # alfa API may not include tags in acSubmission
                solved_at=timestamp,
                time_seconds=None,
                submission_id=str(s.get("id", "")),
            )
        # Contest history for rating (if API returns it)
        try:
            history = LeetCodeAPI.contest_history(username)
            if isinstance(history, list):
                for h in history:
                    contest_slug = str(h.get("titleSlug", h.get("contest", {}).get("title", "")))
                    add_rating_change(
                        platform="leetcode",
                        user_id=user_id,
                        contest_id=contest_slug,
                        old_rating=int(h.get("rating", 0) or 0),
                        new_rating=int(h.get("rating", 0) or 0),
                        timestamp=int(h.get("finishTime", time.time())),
                    )
        except Exception:
            pass
    except Exception as e:
        logger.exception("LC practice sync failed: %s", e)


def run_practice_sync(user_id: str = USER_ID_DEFAULT) -> None:
    config = get_or_create_user_config(user_id)
    cf_handle = config.get("codeforces_handle") or ""
    lc_username = config.get("leetcode_username") or ""
    if cf_handle:
        _sync_codeforces(cf_handle, user_id)
    if lc_username:
        _sync_leetcode(lc_username, user_id)
