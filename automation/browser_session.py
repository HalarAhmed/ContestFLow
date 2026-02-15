"""Shared persistent browser session for Playwright automation.

Uses launch_persistent_context so cookies/login state are saved to disk.
After logging in once (via login_once.py), all future automation reuses the session.
"""
import os
from pathlib import Path

from utils.logging import get_logger

logger = get_logger(__name__)

# Session data lives next to the project (not inside it, to avoid git issues).
_SESSION_DIR = os.environ.get(
    "PLAYWRIGHT_SESSION_DIR",
    str(Path(__file__).resolve().parents[1] / ".playwright-sessions"),
)


def get_session_dir(platform: str) -> str:
    """Return the session directory for a platform (e.g. 'codeforces' or 'leetcode').
    Creates the directory if it doesn't exist."""
    d = os.path.join(_SESSION_DIR, platform)
    os.makedirs(d, exist_ok=True)
    return d


def has_session(platform: str) -> bool:
    """True if a persistent session directory exists and has data."""
    d = get_session_dir(platform)
    # Playwright writes multiple files; if the dir has anything, session likely exists
    try:
        return len(os.listdir(d)) > 0
    except OSError:
        return False
