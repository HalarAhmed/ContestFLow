"""Recommend ~10 real practice problems from Codeforces and LeetCode based on user history."""
import random
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)


def get_recommended_problems(user_id: str = "default", count: int = 10) -> list[dict[str, Any]]:
    """Return up to `count` recommended problems with name, url, difficulty, tags, platform."""
    solved_ids: set[str] = set()
    weak_tags: list[str] = []
    difficulty_low = 800
    difficulty_high = 1400

    # Gather user history from DB
    try:
        from db.dal import get_practice_solves
        solves = get_practice_solves(user_id=user_id)
        for s in solves:
            solved_ids.add(s.get("problem_id", ""))
    except Exception as e:
        logger.warning("Could not load practice solves: %s", e)

    # Get weak tags and difficulty range from analytics
    try:
        from analytics.recommendations import get_weak_strong_tags
        tag_data = get_weak_strong_tags(user_id, use_cache=True)
        weak_tags = tag_data.get("weak_tags", [])
    except Exception:
        pass

    try:
        from db.dal import get_rating_history
        rating_history = get_rating_history(user_id, limit=5)
        max_rating = 0
        for r in rating_history:
            max_rating = max(max_rating, int(r.get("new_rating", 0)))
        if max_rating:
            difficulty_high = max(1200, min(2000, max_rating + 200))
            difficulty_low = max(800, difficulty_high - 400)
    except Exception:
        pass

    problems: list[dict] = []

    # --- Codeforces problems (~7) ---
    cf_target = min(7, count)
    try:
        from integrations.codeforces import CodeforcesAPI
        data = CodeforcesAPI.problemset_problems()
        all_problems = data.get("problems", []) if isinstance(data, dict) else []
        stats = data.get("problemStatistics", []) if isinstance(data, dict) else []

        # Build solved-count map for popularity
        solved_count_map: dict[str, int] = {}
        for s in stats:
            pid = f"{s.get('contestId', '')}_{s.get('index', '')}"
            solved_count_map[pid] = s.get("solvedCount", 0)

        # Filter candidates
        candidates = []
        for p in all_problems:
            pid = f"{p.get('contestId', '')}_{p.get('index', '')}"
            if pid in solved_ids:
                continue
            rating = p.get("rating")
            if rating is None:
                continue
            if not (difficulty_low <= rating <= difficulty_high):
                continue
            tags = p.get("tags", [])
            candidates.append({
                "problem_id": pid,
                "name": p.get("name", pid),
                "url": f"https://codeforces.com/problemset/problem/{p.get('contestId')}/{p.get('index')}",
                "difficulty": str(rating),
                "tags": tags,
                "platform": "codeforces",
                "solved_count": solved_count_map.get(pid, 0),
            })

        # Prioritize problems matching weak tags
        weak_set = set(weak_tags)
        weak_matches = [c for c in candidates if weak_set & set(c["tags"])]
        other = [c for c in candidates if not (weak_set & set(c["tags"]))]

        picked: list[dict] = []
        # Pick up to half from weak-tag matches
        weak_pick = min(len(weak_matches), cf_target // 2 + 1)
        if weak_matches:
            picked.extend(random.sample(weak_matches, weak_pick))

        remaining = cf_target - len(picked)
        if remaining > 0 and other:
            picked.extend(random.sample(other, min(remaining, len(other))))

        for p in picked:
            p.pop("solved_count", None)
            p.pop("problem_id", None)
        problems.extend(picked[:cf_target])
    except Exception as e:
        logger.warning("CF problem recommendation failed: %s", e)

    # --- LeetCode problems (~3) ---
    lc_target = count - len(problems)
    if lc_target > 0:
        try:
            from integrations.leetcode import LeetCodeAPI
            # Use the user's AC submissions to find what they haven't solved
            # Then pick random unsolved from a tag-based query
            # The alfa API doesn't have a problemset endpoint, so we use
            # a simple approach: fetch some problems via select endpoint
            import httpx
            resp = httpx.get(
                "https://alfa-leetcode-api.onrender.com/problems",
                params={"limit": 100},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                problem_list = data.get("problemsetQuestionList", data) if isinstance(data, dict) else data
                if isinstance(problem_list, list):
                    lc_solved = {s.get("problem_id", "") for s in get_practice_solves_safe(user_id, "leetcode")}
                    lc_candidates = []
                    for p in problem_list:
                        slug = p.get("titleSlug", "")
                        if slug in lc_solved:
                            continue
                        diff = (p.get("difficulty") or "").capitalize()
                        lc_candidates.append({
                            "name": p.get("title", slug),
                            "url": f"https://leetcode.com/problems/{slug}/",
                            "difficulty": diff,
                            "tags": [t.get("name", t) if isinstance(t, dict) else str(t) for t in (p.get("topicTags") or [])],
                            "platform": "leetcode",
                        })
                    if lc_candidates:
                        problems.extend(random.sample(lc_candidates, min(lc_target, len(lc_candidates))))
        except Exception as e:
            logger.warning("LC problem recommendation failed: %s", e)

    return problems[:count]


def get_practice_solves_safe(user_id: str, platform: str) -> list[dict]:
    """Get practice solves, returning empty list on failure."""
    try:
        from db.dal import get_practice_solves
        return get_practice_solves(user_id=user_id, platform=platform)
    except Exception:
        return []
