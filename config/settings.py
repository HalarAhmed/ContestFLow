"""Load settings from environment. User handles are set in the dashboard (user_config in DB); env is optional fallback."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


class Settings:
    # MongoDB
    MONGODB_URI: str = _str("MONGODB_URI", "mongodb://localhost:27017")
    MONGODB_DB: str = _str("MONGODB_DB", "cp_assistant")

    # Platform handles: optional env fallback; each user sets their own in the dashboard (stored in user_config).
    CODEFORCES_HANDLE: str = _str("CODEFORCES_HANDLE", "")
    LEETCODE_USERNAME: str = _str("LEETCODE_USERNAME", "")

    # Timezone
    USER_TIMEZONE: str = _str("USER_TIMEZONE", "UTC")

    # SMTP
    SMTP_HOST: str = _str("SMTP_HOST", "")
    SMTP_PORT: int = _int("SMTP_PORT", 587)
    SMTP_USER: str = _str("SMTP_USER", "")
    SMTP_PASSWORD: str = _str("SMTP_PASSWORD", "")
    NOTIFICATION_EMAIL: str = _str("NOTIFICATION_EMAIL", "")

    # Credentials for auto-registration (secret)
    CODEFORCES_PASSWORD: str = _str("CODEFORCES_PASSWORD", "")
    LEETCODE_PASSWORD: str = _str("LEETCODE_PASSWORD", "")

    # LLM
    OPENAI_API_KEY: str = _str("OPENAI_API_KEY", "")
    MISTRAL_API_KEY: str = _str("MISTRAL_API_KEY", "")
    MISTRAL_API_KEY: str = _str("MISTRAL_API_KEY", "")

    # Logging
    LOG_LEVEL: str = _str("LOG_LEVEL", "INFO")

    # Use D: or E: when C: is full (Windows only; ignored on Linux/Render)
    CACHE_DRIVE: str = _str("CACHE_DRIVE", "D").upper()
    PLAYWRIGHT_BROWSERS_PATH: str = _str("PLAYWRIGHT_BROWSERS_PATH", "")

    # Registration: use visible browser (headless=False) so Codeforces/LeetCode are less likely to block
    # On Render/Linux servers, headless must be True (no display available)
    REGISTER_HEADLESS: bool = _str("REGISTER_HEADLESS", "false").lower() in ("true", "1", "yes")


def _is_windows() -> bool:
    return os.name == "nt"


def _cache_root() -> str:
    if not _is_windows():
        return os.path.join(os.path.expanduser("~"), ".cp-assistant-cache")
    drive = settings.CACHE_DRIVE or "D"
    if drive not in ("D", "E"):
        drive = "D"
    return f"{drive}:\\cp-assistant-cache"


def get_playwright_browsers_path() -> str:
    if settings.PLAYWRIGHT_BROWSERS_PATH:
        return settings.PLAYWRIGHT_BROWSERS_PATH
    return os.path.join(_cache_root(), "playwright-browsers")


def _playwright_path_has_browser(path: str) -> bool:
    """True if path exists and looks like a Playwright browsers dir (has chromium-*)."""
    if not path or not os.path.isdir(path):
        return False
    try:
        return any(
            d.startswith("chromium-") and os.path.isdir(os.path.join(path, d))
            for d in os.listdir(path)
        )
    except OSError:
        return False


settings = Settings()

# Only set PLAYWRIGHT_BROWSERS_PATH if the custom path exists and has browsers.
# Otherwise leave unset so Playwright uses its default (e.g. after "playwright install chromium").
_custom_pw_path = get_playwright_browsers_path()
if _playwright_path_has_browser(_custom_pw_path):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _custom_pw_path
else:
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

# On Windows, MongoDB Atlas often fails with TLSV1_ALERT_INTERNAL_ERROR unless
# SSL uses a known CA bundle. Set these before any connection so the ssl module uses certifi.
if os.name == "nt" and "mongodb+srv" in (os.environ.get("MONGODB_URI") or ""):
    try:
        import certifi
        _ca = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", _ca)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
    except ImportError:
        pass
