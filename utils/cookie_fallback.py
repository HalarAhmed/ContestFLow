"""Store/load browser cookies in a local file when MongoDB is unavailable (e.g. SSL failure on Windows)."""
import json
import os
from pathlib import Path

_COOKIE_DIR = Path(__file__).resolve().parents[1] / ".playwright-sessions"


def _cookie_file(platform: str) -> Path:
    os.makedirs(_COOKIE_DIR, exist_ok=True)
    return _COOKIE_DIR / f"{platform}_cookies.json"


def save_cookies_fallback(platform: str, cookies: list[dict]) -> None:
    """Save cookies to a local JSON file."""
    path = _cookie_file(platform)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=0)


def load_cookies_fallback(platform: str) -> list[dict] | None:
    """Load cookies from local file. Returns None if file missing or invalid."""
    path = _cookie_file(platform)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, OSError):
        return None
