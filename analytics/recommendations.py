"""Compute weak/strong tags and recommended practice from practice_solves and contest_results."""
from collections import Counter, defaultdict
from typing import Any

from db.dal import get_analytics_cache, get_practice_solves, get_rating_history, set_analytics_cache
from db.collections import USER_ID_DEFAULT
from utils.logging import get_logger

logger = get_logger(__name__)

# Common competitive programming tags to check against
_COMMON_TAGS = [
    "dp", "greedy", "math", "graphs", "binary search", "sorting",
    "trees", "strings", "number theory", "geometry", "data structures",
    "implementation", "brute force", "constructive algorithms",
    "two pointers", "dfs and similar", "bitmasks", "combinatorics",
    "dsu", "shortest paths", "hashing", "divide and conquer",
]


def get_weak_strong_tags(user_id: str = USER_ID_DEFAULT, use_cache: bool = True) -> dict[str, Any]:
    """Returns { weak_tags, strong_tags, tag_counts, tag_avg_difficulty, total_solved }.

    Weak = tags with fewest solves (least practiced areas).
    Strong = tags with most solves AND higher average difficulty.
    """
    if use_cache:
        cached = get_analytics_cache(user_id)
        if cached and cached.get("weak_tags") is not None:
            return {
                "weak_tags": cached.get("weak_tags", []),
                "strong_tags": cached.get("strong_tags", []),
                "tag_counts": cached.get("tag_counts", {}),
                "tag_avg_difficulty": cached.get("tag_avg_difficulty", {}),
                "total_solved": cached.get("total_solved", 0),
            }
    solves = get_practice_solves(user_id=user_id)
    if not solves:
        return {"weak_tags": [], "strong_tags": [], "tag_counts": {}, "tag_avg_difficulty": {}, "total_solved": 0}

    tag_counts: Counter = Counter()
    tag_difficulty_sum: defaultdict[str, float] = defaultdict(float)
    tag_difficulty_count: defaultdict[str, int] = defaultdict(int)

    for s in solves:
        tags = s.get("tags") or []
        diff_str = s.get("difficulty", "")
        diff_val = 0
        try:
            diff_val = int(diff_str)
        except (ValueError, TypeError):
            # LeetCode difficulties: Easy=800, Medium=1200, Hard=1600 (approximate CF scale)
            mapping = {"easy": 800, "medium": 1200, "hard": 1600}
            diff_val = mapping.get(str(diff_str).lower(), 0)

        for t in tags:
            tag_counts[t] += 1
            if diff_val > 0:
                tag_difficulty_sum[t] += diff_val
                tag_difficulty_count[t] += 1

    # Compute average difficulty per tag
    tag_avg_difficulty: dict[str, int] = {}
    for t in tag_counts:
        if tag_difficulty_count[t] > 0:
            tag_avg_difficulty[t] = int(tag_difficulty_sum[t] / tag_difficulty_count[t])

    # Strong: most solved tags (quantity indicates experience)
    sorted_by_count = sorted(tag_counts.items(), key=lambda x: (-x[1], -(tag_avg_difficulty.get(x[0], 0))))
    strong = [t for t, _ in sorted_by_count[:5]]

    # Weak: tags the user has barely touched, or common tags they haven't practiced
    solved_tag_set = set(tag_counts.keys())
    # First, common tags never attempted
    never_tried = [t for t in _COMMON_TAGS if t not in solved_tag_set][:3]
    # Then, least-practiced tags they have attempted (at least once)
    sorted_asc = sorted(tag_counts.items(), key=lambda x: (x[1], tag_avg_difficulty.get(x[0], 0)))
    least_practiced = [t for t, _ in sorted_asc if t not in strong][:5]
    weak = (never_tried + least_practiced)[:5]

    result = {
        "weak_tags": weak,
        "strong_tags": strong,
        "tag_counts": dict(tag_counts),
        "tag_avg_difficulty": tag_avg_difficulty,
        "total_solved": len(solves),
    }
    set_analytics_cache(user_id, result)
    return result


def get_recommended_practice_plan(user_id: str = USER_ID_DEFAULT) -> dict[str, Any]:
    """Returns suggested problems count per tag and difficulty range."""
    data = get_weak_strong_tags(user_id, use_cache=True)
    weak = data.get("weak_tags") or []
    strong = data.get("strong_tags") or []
    rating_history = get_rating_history(user_id, limit=5)
    max_rating = 0
    for r in rating_history:
        max_rating = max(max_rating, int(r.get("new_rating", 0)))
    difficulty_ceiling = max(1200, min(1800, max_rating + 200)) if max_rating else 1400
    low = max(800, difficulty_ceiling - 300)
    return {
        "weak_tags": weak,
        "strong_tags": strong,
        "difficulty_range": [low, difficulty_ceiling],
        "suggested_per_tag": {t: 2 for t in weak},
        "problems_today": [
            f"2 problems in {low}-{difficulty_ceiling} range",
            *[f"1 {t} problem" for t in weak[:2]],
            "Focus on solving within 25 minutes",
        ],
    }
