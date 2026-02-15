"""FastAPI app: dashboard and API for CP Assistant."""
import html as html_module
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from fastapi import Body, FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from config import settings
from db.client import get_db, ensure_indexes
from utils.logging import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

# Overview cache: filled by background thread at startup so GET / is fast.
_overview_cache = {"profile": {"codeforces": {}, "leetcode": {}}, "contests": [], "ts": 0}
_overview_cache_lock = threading.Lock()
CACHE_MAX_AGE = 600


def _refresh_overview_cache() -> None:
    """Fetch profile + contests and update cache. Run in background."""
    try:
        profile, contests = _fetch_overview_with_timeout(timeout_sec=35)
        with _overview_cache_lock:
            _overview_cache["profile"] = profile
            _overview_cache["contests"] = contests
            _overview_cache["ts"] = int(time.time())
        logger.info("Overview cache updated: %s contests", len(contests))
    except Exception as e:
        logger.exception("Overview cache failed: %s", e)


def _start_background_scheduler() -> None:
    """Start APScheduler in a background thread so scheduled jobs run inside the web process.
    This is essential for single-process deployments like Render."""
    import os
    if os.environ.get("DISABLE_SCHEDULER", "").lower() in ("true", "1", "yes"):
        logger.info("Background scheduler disabled via DISABLE_SCHEDULER env var")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        from jobs.contest_monitor import run_contest_monitor
        from jobs.practice_sync import run_practice_sync
        from jobs.post_contest import run_post_contest_analysis

        def _safe(fn, name):
            def wrapper():
                try:
                    fn("default")
                except Exception as e:
                    logger.exception("Scheduled job %s failed: %s", name, e)
            return wrapper

        scheduler = BackgroundScheduler()
        scheduler.add_job(_safe(run_contest_monitor, "contest_monitor"), IntervalTrigger(minutes=30), id="contest_monitor")
        scheduler.add_job(_safe(run_practice_sync, "practice_sync"), IntervalTrigger(hours=6), id="practice_sync")
        scheduler.add_job(_safe(run_post_contest_analysis, "post_contest"), IntervalTrigger(hours=1), id="post_contest")
        scheduler.start()
        logger.info("Background scheduler started: contest monitor/30m, practice sync/6h, post-contest/1h")
    except Exception as e:
        logger.warning("Background scheduler failed to start: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ensure_indexes()
    except Exception as e:
        logger.warning("Index ensure failed (MongoDB may be down): %s", e)
    t = threading.Thread(target=_refresh_overview_cache, daemon=True)
    t.start()
    logger.info("Overview cache thread started")
    _start_background_scheduler()
    yield


app = FastAPI(title="CP Assistant", lifespan=lifespan)


# --- Health (for startup wait). ---
@app.get("/api/health")
def api_health():
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Avoid 404 when the browser requests favicon."""
    return Response(status_code=204)


# --- Data endpoints (JSON for dashboard). Fallback to live APIs when DB empty/unavailable. ---
@app.get("/api/user-config")
def api_user_config():
    try:
        from db.dal import get_or_create_user_config
        return get_or_create_user_config("default")
    except Exception as e:
        logger.warning("user-config from DB failed, using env: %s", e)
    return {
        "user_id": "default",
        "codeforces_handle": settings.CODEFORCES_HANDLE,
        "leetcode_username": settings.LEETCODE_USERNAME,
        "timezone": settings.USER_TIMEZONE,
        "target_rating": 1600,
        "notification": {"email": settings.NOTIFICATION_EMAIL or "", "reminders": ["new_contest", "24h", "1h", "15m"]},
    }


@app.get("/api/contests/upcoming")
def api_upcoming_contests(platform: str | None = None):
    try:
        from db.dal import get_upcoming_contests
        out = get_upcoming_contests(platform=platform)
        if out:
            return out
    except Exception as e:
        logger.warning("upcoming contests from DB failed: %s", e)
    # Live fetch from Codeforces and LeetCode
    try:
        from integrations.codeforces import get_upcoming_cf_contests
        from integrations.leetcode import get_upcoming_lc_contests
        result = []
        if platform != "leetcode":
            for c in get_upcoming_cf_contests()[:10]:
                result.append({
                    "platform": "codeforces",
                    "external_id": str(c.get("id")),
                    "name": c.get("name", "?"),
                    "start_time_utc": c.get("startTimeSeconds", 0),
                    "duration_seconds": c.get("durationSeconds", 0),
                })
        if platform != "codeforces":
            for c in get_upcoming_lc_contests()[:5]:
                result.append({
                    "platform": "leetcode",
                    "external_id": c.get("titleSlug", ""),
                    "name": c.get("title", "?"),
                    "start_time_utc": c.get("startTime", 0),
                    "duration_seconds": c.get("duration", 5400),
                })
        result.sort(key=lambda x: x["start_time_utc"])
        return result
    except Exception as e:
        logger.exception("Live contests fetch failed: %s", e)
        return []


def _get_handles() -> tuple[str, str]:
    """Return (cf_handle, lc_username) from DB config (user's own), .env as last resort."""
    try:
        from db.dal import get_user_config
        config = get_user_config("default")
        if config:
            cf = config.get("codeforces_handle", "")
            lc = config.get("leetcode_username", "")
            if cf and lc:
                return cf, lc
            # One might be set in DB, fall through for the other
            return cf or settings.CODEFORCES_HANDLE, lc or settings.LEETCODE_USERNAME
    except Exception:
        pass
    return settings.CODEFORCES_HANDLE, settings.LEETCODE_USERNAME


@app.get("/api/profile/live")
def api_profile_live():
    """Fetch current stats from Codeforces and LeetCode APIs (no DB)."""
    cf_handle, lc_username = _get_handles()
    out = {"codeforces": {}, "leetcode": {}}
    try:
        from integrations.codeforces import CodeforcesAPI
        info = CodeforcesAPI.user_info(cf_handle)
        if info:
            u = info[0]
            out["codeforces"] = {
                "handle": u.get("handle"),
                "rating": u.get("rating"),
                "maxRating": u.get("maxRating"),
                "rank": u.get("rank"),
                "maxRank": u.get("maxRank"),
                "contribution": u.get("contribution"),
                "friendOfCount": u.get("friendOfCount"),
            }
    except Exception as e:
        logger.warning("CF profile failed: %s", e)
    try:
        from integrations.leetcode import LeetCodeAPI
        profile = LeetCodeAPI.profile(lc_username)
        out["leetcode"] = {
            "username": lc_username,
            "totalSolved": profile.get("totalSolved"),
            "easySolved": profile.get("easySolved"),
            "mediumSolved": profile.get("mediumSolved"),
            "hardSolved": profile.get("hardSolved"),
            "totalEasy": profile.get("totalEasy"),
            "totalMedium": profile.get("totalMedium"),
            "totalHard": profile.get("totalHard"),
        }
    except Exception as e:
        logger.warning("LC profile failed: %s", e)
    return out


@app.get("/api/practice/summary")
def api_practice_summary(days: int = 30):
    try:
        from db.dal import get_practice_solves
        to_ts = int(time.time())
        from_ts = to_ts - days * 86400
        solves = get_practice_solves(user_id="default", from_ts=from_ts, to_ts=to_ts)
        by_platform = {}
        for s in solves:
            p = s.get("platform", "?")
            by_platform[p] = by_platform.get(p, 0) + 1
        return {"total": len(solves), "by_platform": by_platform, "solves": solves[:100]}
    except Exception as e:
        logger.warning("practice summary from DB failed: %s", e)
    # Fallback: use live profile for total solved
    try:
        live = api_profile_live()
        total = 0
        by_platform = {}
        if live.get("leetcode", {}).get("totalSolved") is not None:
            total = live["leetcode"]["totalSolved"]
            by_platform["leetcode"] = total
        if live.get("codeforces", {}).get("rating") is not None:
            by_platform["codeforces"] = "(see profile)"
        return {"total": total, "by_platform": by_platform, "solves": [], "source": "live"}
    except Exception:
        return {"total": 0, "by_platform": {}, "solves": []}


@app.get("/api/analytics/weak-strong-tags")
def api_weak_strong_tags():
    try:
        from analytics.recommendations import get_weak_strong_tags
        return get_weak_strong_tags("default", use_cache=False)
    except Exception as e:
        logger.warning("weak-strong-tags failed: %s", e)
    return {"weak_tags": [], "strong_tags": [], "tag_counts": {}, "total_solved": 0}


@app.get("/api/analytics/training-plan")
def api_training_plan():
    try:
        from analytics.recommendations import get_recommended_practice_plan
        return get_recommended_practice_plan("default")
    except Exception as e:
        logger.warning("training-plan failed: %s", e)
    return {
        "weak_tags": [],
        "strong_tags": [],
        "difficulty_range": [1200, 1600],
        "problems_today": ["2 problems in 1200-1600 range", "1 DP problem", "Focus on solving within 25 minutes"],
    }


@app.get("/api/rating-history")
def api_rating_history(platform: str | None = None, limit: int = 50):
    try:
        from db.dal import get_rating_history
        return get_rating_history("default", platform=platform, limit=limit)
    except Exception:
        return []


@app.post("/api/update-data")
def api_update_data():
    """Trigger practice sync and analytics refresh. Returns 200 with status/message even when MongoDB is down."""
    from jobs.practice_sync import run_practice_sync
    from jobs.contest_monitor import run_contest_monitor
    try:
        run_practice_sync("default")
        run_contest_monitor("default")
        return {"status": "ok", "message": "Sync completed"}
    except Exception as e:
        err = str(e)
        logger.exception("Update data failed: %s", e)
        try:
            from pymongo.errors import ServerSelectionTimeoutError
            if isinstance(e, ServerSelectionTimeoutError):
                return {"status": "error", "message": "MongoDB is not running. Start it: run scripts\\start_mongodb.ps1 or start the MongoDB service."}
        except ImportError:
            pass
        if "pymongo" in type(e).__module__.lower() or "27017" in err or "mongo" in err.lower():
            return {"status": "error", "message": "MongoDB is not running. Start it: run scripts\\start_mongodb.ps1 or start the MongoDB service."}
        return {"status": "error", "message": err}


@app.get("/api/session/status")
def api_session_status():
    """Return whether we have stored cookies for each platform (so Register can skip login)."""
    try:
        from db.dal import get_browser_cookies
        return {
            "codeforces": get_browser_cookies("default", "codeforces") is not None,
            "leetcode": get_browser_cookies("default", "leetcode") is not None,
        }
    except Exception:
        return {"codeforces": False, "leetcode": False}


@app.post("/api/session/import")
def api_session_import(payload: dict = Body(default=None)):
    """
    Import cookies pasted from the user's browser (after they log in normally).
    Body: { "platform": "codeforces"|"leetcode", "cookies": "paste here" }
    """
    payload = payload or {}
    platform = (payload.get("platform") or "").strip().lower()
    cookies = (payload.get("cookies") or "").strip()
    if platform not in ("codeforces", "leetcode"):
        return {"success": False, "message": "Platform must be codeforces or leetcode"}
    if not cookies:
        return {"success": False, "message": "Paste your cookies in the text area"}
    try:
        from utils.cookies import parse_cookies_raw
        parsed = parse_cookies_raw(cookies, platform)
        if not parsed:
            hint = "No valid cookies for this platform. "
            if cookies.strip().startswith("{") and '"data"' in cookies[:200]:
                hint += "Cookie-Editor 'Backup' format is often encrypted. Use 'Export' → 'JSON' (array of cookies) or 'Netscape' instead."
            else:
                hint += "Use Netscape format or a JSON array of cookies; domain must be codeforces.com or leetcode.com."
            return {"success": False, "message": hint}
        try:
            from db.dal import set_browser_cookies
            set_browser_cookies("default", platform, parsed)
            return {"success": True, "message": f"Saved {len(parsed)} cookies for {platform}. You can use Register (auto) now."}
        except Exception as db_err:
            # MongoDB down (e.g. SSL handshake failure on Windows) — save locally so Register still works
            from utils.cookie_fallback import save_cookies_fallback
            save_cookies_fallback(platform, parsed)
            logger.warning("MongoDB unavailable (%s); saved cookies to local file", db_err)
            return {"success": True, "message": f"Saved {len(parsed)} cookies locally (MongoDB unavailable). Register (auto) will use them."}
    except Exception as e:
        logger.exception("Cookie import failed: %s", e)
        return {"success": False, "message": str(e)[:200]}


@app.post("/api/register")
def api_register(platform: str = "", contest_id: str = ""):
    """Request registration for a contest (calls Playwright). Always returns 200 with success/message."""
    try:
        try:
            from db.dal import get_or_create_user_config, get_user_passwords
            config = get_or_create_user_config("default")
            passwords = get_user_passwords("default")
        except Exception:
            config = {
                "codeforces_handle": settings.CODEFORCES_HANDLE,
                "leetcode_username": settings.LEETCODE_USERNAME,
            }
            passwords = {}

        cf_password = passwords.get("codeforces_password") or settings.CODEFORCES_PASSWORD or ""
        lc_password = passwords.get("leetcode_password") or settings.LEETCODE_PASSWORD or ""

        # Record registration attempt in DB
        reg_id = None
        try:
            from db.dal import add_registration
            reg_id = add_registration("default", contest_id, status="pending")
        except Exception:
            pass

        if platform == "codeforces":
            from automation.register_cf import register_codeforces
            ok, msg = register_codeforces(
                contest_id,
                config.get("codeforces_handle", "") or settings.CODEFORCES_HANDLE,
                cf_password,
            )
        elif platform == "leetcode":
            from automation.register_leetcode import register_leetcode
            ok, msg = register_leetcode(
                contest_id,
                config.get("leetcode_username", "") or settings.LEETCODE_USERNAME,
                lc_password,
            )
        else:
            return {"success": False, "message": "Unknown platform"}

        # Persist registration result
        try:
            from db.dal import update_registration_status
            status = "success" if ok else "failed"
            update_registration_status(reg_id, contest_id, "default", status, "" if ok else msg[:200])
        except Exception:
            pass

        out_msg = msg if len(msg) <= 300 else msg[:300] + "..."
        return {"success": ok, "message": out_msg}
    except Exception as e:
        logger.exception("Register failed: %s", e)
        out_msg = str(e)
        if len(out_msg) > 300:
            out_msg = out_msg[:300] + "..."
        return {"success": False, "message": out_msg}


@app.post("/api/setup")
async def api_setup(
    codeforces_handle: str = Form(""),
    leetcode_username: str = Form(""),
    cf_cookies_file: UploadFile | None = File(None),
    lc_cookies_file: UploadFile | None = File(None),
):
    """Save user account details and cookies from the onboarding form."""
    errors = []
    if not codeforces_handle.strip():
        errors.append("Codeforces handle is required.")
    if not leetcode_username.strip():
        errors.append("LeetCode username is required.")
    if not cf_cookies_file or not cf_cookies_file.filename:
        errors.append("Codeforces cookie file is required.")
    if not lc_cookies_file or not lc_cookies_file.filename:
        errors.append("LeetCode cookie file is required.")
    if errors:
        return HTMLResponse(
            f'<div style="font-family:sans-serif;padding:40px;background:#0c0f17;color:#e6e9f0;min-height:100vh;display:flex;align-items:center;justify-content:center;">'
            f'<div style="max-width:420px;text-align:center;">'
            f'<h2 style="color:#ef4444;">Setup incomplete</h2>'
            f'<ul style="text-align:left;color:#8b92a5;">{"".join("<li>" + html_module.escape(e) + "</li>" for e in errors)}</ul>'
            f'<a href="/setup" style="color:#6366f1;font-weight:600;">Go back</a></div></div>',
            status_code=400,
        )
    try:
        from db.dal import upsert_user_config, set_browser_cookies
        upsert_user_config(
            "default",
            codeforces_handle=codeforces_handle.strip(),
            leetcode_username=leetcode_username.strip(),
        )

        # Parse and store cookie files
        cookie_errors = []
        for upload, platform in [(cf_cookies_file, "codeforces"), (lc_cookies_file, "leetcode")]:
            raw = await upload.read()
            text = raw.decode("utf-8", errors="ignore").strip()
            if not text:
                cookie_errors.append(f"{platform.title()} cookie file is empty.")
                continue
            try:
                from utils.cookies import parse_cookies_raw
                parsed = parse_cookies_raw(text, platform)
                if not parsed:
                    cookie_errors.append(f"No valid {platform} cookies found in the file. Make sure you exported from J2TEAM Cookies while logged in.")
                    continue
                try:
                    set_browser_cookies("default", platform, parsed)
                except Exception:
                    from utils.cookie_fallback import save_cookies_fallback
                    save_cookies_fallback(platform, parsed)
            except Exception as e:
                cookie_errors.append(f"{platform.title()} cookie parsing failed: {str(e)[:100]}")

        if cookie_errors:
            return HTMLResponse(
                f'<div style="font-family:sans-serif;padding:40px;background:#0c0f17;color:#e6e9f0;min-height:100vh;display:flex;align-items:center;justify-content:center;">'
                f'<div style="max-width:420px;text-align:center;">'
                f'<h2 style="color:#ef4444;">Cookie import issues</h2>'
                f'<ul style="text-align:left;color:#8b92a5;">{"".join("<li>" + html_module.escape(e) + "</li>" for e in cookie_errors)}</ul>'
                f'<p style="color:#8b92a5;font-size:0.85rem;">Your handles were saved. Please re-upload valid cookie files.</p>'
                f'<a href="/setup" style="color:#6366f1;font-weight:600;">Go back</a></div></div>',
                status_code=400,
            )

        # Trigger an overview cache refresh so dashboard loads with the new user's data
        threading.Thread(target=_refresh_overview_cache, daemon=True).start()
        return RedirectResponse(url="/", status_code=302)
    except Exception as e:
        logger.exception("Setup failed: %s", e)
        return HTMLResponse(
            f'<div style="font-family:sans-serif;padding:40px;background:#0c0f17;color:#e6e9f0;min-height:100vh;display:flex;align-items:center;justify-content:center;">'
            f'<div style="max-width:420px;text-align:center;">'
            f'<h2 style="color:#ef4444;">Setup failed</h2><p style="color:#8b92a5;">{html_module.escape(str(e)[:200])}</p>'
            f'<a href="/setup" style="color:#6366f1;font-weight:600;">Try again</a></div></div>',
            status_code=500,
        )


@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    """Show the onboarding / settings form."""
    cf_handle = ""
    lc_username = ""
    try:
        from db.dal import get_user_config
        config = get_user_config("default")
        if config:
            cf_handle = config.get("codeforces_handle", "") or ""
            lc_username = config.get("leetcode_username", "") or ""
    except Exception:
        pass
    html = SETUP_HTML.replace("__CF_HANDLE__", html_module.escape(cf_handle))
    html = html.replace("__LC_USERNAME__", html_module.escape(lc_username))
    return HTMLResponse(html)


@app.get("/api/registrations")
def api_registrations():
    """Return the user's contest registration history."""
    try:
        from db.dal import get_registrations
        return get_registrations("default")
    except Exception as e:
        logger.warning("registrations failed: %s", e)
        return []


@app.get("/api/practice/recommended")
def api_practice_recommended():
    """Return ~10 recommended practice problems."""
    try:
        from utils.problem_recommender import get_recommended_problems
        return get_recommended_problems("default", count=10)
    except Exception as e:
        logger.warning("practice recommended failed: %s", e)
        return []


@app.post("/api/chat")
def api_chat(payload: dict = Body(default=None)):
    """Chat with the Mistral-powered CP tutor."""
    payload = payload or {}
    user_message = (payload.get("message") or "").strip()
    history = payload.get("history") or []
    show_solution = payload.get("show_solution", False)

    if not user_message:
        return {"reply": "Please type a message."}

    api_key = settings.MISTRAL_API_KEY
    if not api_key:
        return {"reply": "Mistral API key not configured. Add it in Settings or .env (MISTRAL_API_KEY)."}

    if show_solution:
        system_msg = (
            "You are a competitive programming tutor and expert problem solver. "
            "The user has enabled Solution mode. Provide a complete, well-explained solution with code "
            "(preferably C++ or Python). Explain the approach, time complexity, and key insights."
        )
    else:
        system_msg = (
            "You are a competitive programming tutor. Your job is to help the user think through problems "
            "without giving away the full solution. Give hints about the approach, suggest which data structure "
            "or algorithm to consider, ask guiding questions, and point out edge cases. "
            "Do NOT reveal the full solution or write complete code. Nudge the user toward the answer."
        )

    messages = [{"role": "system", "content": system_msg}]
    # Add conversation history (last 20 messages max)
    for h in history[-20:]:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    try:
        resp = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "mistral-small-latest", "messages": messages, "temperature": 0.7, "max_tokens": 1024},
            timeout=30,
        )
        if resp.status_code != 200:
            return {"reply": f"Mistral API error ({resp.status_code}): {resp.text[:200]}"}
        data = resp.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "No response.")
        return {"reply": reply}
    except Exception as e:
        logger.warning("Chat API failed: %s", e)
        return {"reply": f"Chat error: {str(e)[:150]}"}


# --- Server-rendered dashboard: fetch data with timeout, build HTML in Python ---
def _cf_rating_class(rating: int | None) -> str:
    if rating is None:
        return ""
    if rating < 1200:
        return "cf-gray"
    if rating < 1400:
        return "cf-green"
    if rating < 1600:
        return "cf-cyan"
    if rating < 1900:
        return "cf-blue"
    if rating < 2100:
        return "cf-violet"
    if rating < 2400:
        return "cf-orange"
    return "cf-red"


def _build_profile_cards_html(profile: dict) -> str:
    """Build profile cards HTML from profile dict. Escapes all text."""
    def esc(s):
        return html_module.escape(str(s)) if s is not None else ""
    cf = profile.get("codeforces") or {}
    lc = profile.get("leetcode") or {}
    handle = esc(cf.get("handle"))
    rating = cf.get("rating")
    max_rating = cf.get("maxRating")
    rank = esc(cf.get("rank"))
    max_rank = esc(cf.get("maxRank"))
    if rating is not None or handle:
        cf_html = (
            f'<div class="profile-card profile-card-cf">'
            f'<div class="profile-card-header"><span class="profile-card-badge">Codeforces</span>'
            f'<a href="https://codeforces.com/profile/{handle}" target="_blank" rel="noopener" class="profile-link">{handle or "—"}</a></div>'
            f'<div class="profile-stats">'
        )
        if rating is not None:
            cf_html += f'<div class="stat-row"><span class="stat-label">Rating</span><span class="stat-value cf-rating {_cf_rating_class(rating)}">{rating}</span></div>'
        if max_rating is not None:
            cf_html += f'<div class="stat-row"><span class="stat-label">Max</span><span class="stat-value cf-rating {_cf_rating_class(max_rating)}">{max_rating}</span></div>'
        if rank:
            cf_html += f'<div class="stat-row"><span class="stat-label">Rank</span><span class="stat-value">{rank}</span></div>'
        if max_rank:
            cf_html += f'<div class="stat-row"><span class="stat-label">Max rank</span><span class="stat-value">{max_rank}</span></div>'
        cf_html += "</div></div>"
    else:
        cf_html = '<div class="profile-card profile-card-cf"><div class="profile-card-header"><span class="profile-card-badge">Codeforces</span></div><div class="card-message card-message-err">Profile not loaded. Check your handle in <a href="/setup" style="color:var(--accent)">Settings</a>.</div></div>'
    username = esc(lc.get("username"))
    total = lc.get("totalSolved")
    easy = lc.get("easySolved")
    medium = lc.get("mediumSolved")
    hard = lc.get("hardSolved")
    total_easy = lc.get("totalEasy")
    total_medium = lc.get("totalMedium")
    total_hard = lc.get("totalHard")
    if total is not None or username:
        lc_html = (
            f'<div class="profile-card profile-card-lc">'
            f'<div class="profile-card-header"><span class="profile-card-badge">LeetCode</span>'
            f'<a href="https://leetcode.com/u/{username}/" target="_blank" rel="noopener" class="profile-link">{username or "—"}</a></div>'
            f'<div class="profile-stats">'
        )
        if total is not None:
            lc_html += f'<div class="stat-row"><span class="stat-label">Solved</span><span class="stat-value">{total}</span></div>'
        if easy is not None:
            lc_html += f'<div class="stat-row"><span class="stat-label">Easy</span><span class="stat-value">{easy}' + (f" / {total_easy}" if total_easy else "") + "</span></div>"
        if medium is not None:
            lc_html += f'<div class="stat-row"><span class="stat-label">Medium</span><span class="stat-value">{medium}' + (f" / {total_medium}" if total_medium else "") + "</span></div>"
        if hard is not None:
            lc_html += f'<div class="stat-row"><span class="stat-label">Hard</span><span class="stat-value">{hard}' + (f" / {total_hard}" if total_hard else "") + "</span></div>"
        lc_html += "</div></div>"
    else:
        lc_html = '<div class="profile-card profile-card-lc"><div class="profile-card-header"><span class="profile-card-badge">LeetCode</span></div><div class="card-message card-message-err">Profile not loaded. Check your username in <a href="/setup" style="color:var(--accent)">Settings</a>.</div></div>'
    return f'<div class="profile-grid">{cf_html}{lc_html}</div>'


def _build_contest_list_html(contests: list) -> str:
    """Build contest list HTML. Escapes text."""
    if not contests:
        return '<div class="card-message card-message-empty">No upcoming contests right now.</div>'
    items = []
    for c in contests[:15]:
        name = html_module.escape(str(c.get("name", "?")))
        platform = html_module.escape(str(c.get("platform", "?"))).replace("'", "\\'")
        ext_id = html_module.escape(str(c.get("external_id", ""))).replace("'", "\\'")
        start_ts = c.get("start_time_utc") or 0
        start_str = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if start_ts else "?"
        if (c.get("platform") or "").lower() == "codeforces":
            reg_url = f"https://codeforces.com/contestRegistration/{c.get('external_id', '')}"
        else:
            reg_url = f"https://leetcode.com/contest/{c.get('external_id', '')}/"
        reg_url = html_module.escape(reg_url)
        items.append(
            f'<div class="contest-row">'
            f'<div class="contest-info"><div class="contest-name">{name}</div><div class="contest-meta">{platform} · {start_str}</div></div>'
            f'<span class="contest-actions">'
            f'<a href="{reg_url}" target="_blank" rel="noopener" class="btn btn-outline">Open page</a> '
            f'<button type="button" class="btn btn-register" onclick="register(\'{platform}\', \'{ext_id}\')">Register (auto)</button>'
            f'</span></div>'
        )
    return "<div class=\"contest-list\">" + "".join(items) + "</div>"


def _fetch_overview_with_timeout(timeout_sec: int = 35) -> tuple[dict, list]:
    """Fetch profile and contests in a thread with timeout. Returns (profile, contests); on error returns empty structures."""
    profile = {"codeforces": {}, "leetcode": {}}
    contests = []
    def _fetch():
        profile.update(api_profile_live())
        contests.extend(api_upcoming_contests())
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_fetch)
            fut.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        logger.warning("Overview fetch timed out after %s sec", timeout_sec)
    except Exception as e:
        logger.exception("Overview fetch failed: %s", e)
    return profile, contests


# Loading page when cache not ready yet (auto-refresh every 4 sec)
LOADING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="4;url=/">
  <title>CP Assistant — Loading</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; padding: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #0c0f17; color: #e6e9f0; }
    .box { text-align: center; padding: 2rem; max-width: 400px; }
    h1 { font-size: 1.5rem; margin-bottom: 1rem; }
    p { color: #8b92a5; font-size: 0.95rem; }
    a { color: #6366f1; }
    .spinner { width: 40px; height: 40px; border: 3px solid #2a3142; border-top-color: #6366f1; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 1.5rem; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="box">
    <div class="spinner"></div>
    <h1>Loading your data</h1>
    <p>Fetching Codeforces & LeetCode… Page will refresh in 4 seconds.</p>
    <p style="margin-top:1rem;">If this persists, ensure you ran <code>run_all.ps1</code> and open <a href="http://localhost:8000">http://localhost:8000</a></p>
  </div>
</body>
</html>"""


# --- Setup / Onboarding HTML ---
SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CP Assistant — Get Started</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap" rel="stylesheet">
  <style>
    :root{--bg:#0c0f17;--surface:#151922;--surface2:#1c212c;--border:#2a3142;--text:#e6e9f0;--text-muted:#8b92a5;--accent:#6366f1;--accent-hover:#4f46e5;--success:#22c55e;--danger:#ef4444;}
    *{box-sizing:border-box;}
    body{font-family:'DM Sans',system-ui,sans-serif;margin:0;padding:0;background:var(--bg);color:var(--text);min-height:100vh;display:flex;align-items:center;justify-content:center;}
    .setup-wrap{max-width:580px;width:100%;margin:24px;display:flex;flex-direction:column;gap:0;}
    .setup-card{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:44px 40px 36px;position:relative;overflow:hidden;}
    .setup-card::before{content:'';position:absolute;top:0;left:0;right:0;height:4px;background:linear-gradient(90deg,var(--accent),var(--success));}
    h1{font-size:1.7rem;font-weight:700;margin:0 0 6px 0;letter-spacing:-0.02em;}
    .subtitle{color:var(--text-muted);font-size:0.9rem;margin-bottom:32px;line-height:1.5;}
    .step-badge{display:inline-flex;align-items:center;gap:8px;font-size:0.75rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--accent);margin-bottom:18px;}
    .step-num{width:24px;height:24px;border-radius:50%;background:var(--accent);color:#fff;display:inline-flex;align-items:center;justify-content:center;font-size:0.7rem;}
    .form-group{margin-bottom:20px;}
    .form-group label{display:block;font-size:0.8rem;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;}
    .form-group input[type="text"]{width:100%;padding:13px 16px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:var(--text);font-family:inherit;font-size:0.92rem;transition:border-color .15s;}
    .form-group input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(99,102,241,0.15);}
    .form-group input.invalid{border-color:var(--danger);}
    .form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
    .divider{height:1px;background:var(--border);margin:28px 0 24px;}
    .cookie-section{background:var(--surface2);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:20px;}
    .how-to{margin:0 0 20px 0;padding:0;list-style:none;counter-reset:steps;}
    .how-to li{counter-increment:steps;display:flex;gap:12px;margin-bottom:14px;font-size:0.88rem;line-height:1.5;color:var(--text);}
    .how-to li::before{content:counter(steps);min-width:26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:700;flex-shrink:0;margin-top:1px;}
    .how-to a{color:var(--accent);text-decoration:none;font-weight:600;}
    .how-to a:hover{text-decoration:underline;}
    .upload-area{border:2px dashed var(--border);border-radius:12px;padding:20px;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;position:relative;}
    .upload-area:hover,.upload-area.dragover{border-color:var(--accent);background:rgba(99,102,241,0.05);}
    .upload-area.has-file{border-color:var(--success);border-style:solid;background:rgba(34,197,94,0.05);}
    .upload-area input{position:absolute;inset:0;opacity:0;cursor:pointer;}
    .upload-icon{font-size:1.6rem;margin-bottom:4px;}
    .upload-label{font-size:0.85rem;color:var(--text-muted);}
    .upload-label strong{color:var(--text);}
    .upload-filename{font-size:0.8rem;color:var(--success);font-weight:600;margin-top:4px;display:none;}
    .upload-area.has-file .upload-filename{display:block;}
    .upload-area.has-file .upload-icon{color:var(--success);}
    .error-msg{display:none;font-size:0.82rem;color:var(--danger);margin-top:6px;}
    .btn-submit{width:100%;margin-top:12px;padding:16px;background:var(--accent);color:#fff;border:none;border-radius:12px;cursor:pointer;font-weight:700;font-size:1.05rem;font-family:inherit;transition:background .15s,transform .1s;letter-spacing:-0.01em;}
    .btn-submit:hover{background:var(--accent-hover);transform:translateY(-1px);}
    .btn-submit:active{transform:translateY(0);}
    .btn-submit:disabled{opacity:0.5;cursor:not-allowed;transform:none;}
    .hint{font-size:0.78rem;color:var(--text-muted);margin-top:8px;line-height:1.5;}
  </style>
</head>
<body>
  <div class="setup-wrap">
    <div class="setup-card">
      <h1>Welcome to CP Assistant</h1>
      <p class="subtitle">Connect your competitive programming accounts to get personalized insights, contest tracking, and AI-powered practice guidance.</p>
      <form id="setupForm" action="/api/setup" method="POST" enctype="multipart/form-data" onsubmit="return validateSetup()">
        <div class="step-badge"><span class="step-num">1</span> Your Handles</div>
        <div class="form-row">
          <div class="form-group">
            <label>Codeforces Handle</label>
            <input type="text" id="cfHandle" name="codeforces_handle" value="__CF_HANDLE__" placeholder="e.g. tourist" required>
          </div>
          <div class="form-group">
            <label>LeetCode Username</label>
            <input type="text" id="lcUser" name="leetcode_username" value="__LC_USERNAME__" placeholder="e.g. neal_wu" required>
          </div>
        </div>
        <div class="divider"></div>
        <div class="step-badge"><span class="step-num">2</span> Import Your Cookies</div>
        <div class="cookie-section">
          <p style="font-size:0.88rem;color:var(--text);margin:0 0 16px 0;font-weight:500;">We need your browser cookies so the app can access your accounts without needing your passwords.</p>
          <ol class="how-to">
            <li>Install the <a href="https://chromewebstore.google.com/detail/j2team-cookies/okpidcojinmlaakglciglbpcpajaibco" target="_blank" rel="noopener">J2TEAM Cookies</a> Chrome extension</li>
            <li>Go to <a href="https://codeforces.com" target="_blank">codeforces.com</a> and <strong>log in</strong> to your account</li>
            <li>Click the J2TEAM Cookies icon and click <strong>"Export"</strong> to save the cookie file</li>
            <li>Repeat for <a href="https://leetcode.com" target="_blank">leetcode.com</a> &mdash; log in, then export cookies</li>
            <li>Upload both cookie files below</li>
          </ol>
          <div class="form-row">
            <div class="form-group">
              <label>Codeforces Cookies</label>
              <div class="upload-area" id="cfUpload">
                <input type="file" name="cf_cookies_file" id="cfFile" accept=".json,.txt" required>
                <div class="upload-icon">&#128196;</div>
                <div class="upload-label"><strong>Choose file</strong> or drag here</div>
                <div class="upload-filename" id="cfFileName"></div>
              </div>
              <div class="error-msg" id="cfError">Codeforces cookie file is required</div>
            </div>
            <div class="form-group">
              <label>LeetCode Cookies</label>
              <div class="upload-area" id="lcUpload">
                <input type="file" name="lc_cookies_file" id="lcFile" accept=".json,.txt" required>
                <div class="upload-icon">&#128196;</div>
                <div class="upload-label"><strong>Choose file</strong> or drag here</div>
                <div class="upload-filename" id="lcFileName"></div>
              </div>
              <div class="error-msg" id="lcError">LeetCode cookie file is required</div>
            </div>
          </div>
        </div>
        <button type="submit" class="btn-submit" id="submitBtn">Get Started</button>
        <p class="hint" style="text-align:center;margin-top:14px;">Your cookies are stored locally and used only for contest registration and data sync. No passwords leave your machine.</p>
      </form>
    </div>
  </div>
  <script>
    // File upload UI
    function setupUpload(areaId, inputId, nameId, errorId) {
      var area = document.getElementById(areaId);
      var input = document.getElementById(inputId);
      var nameEl = document.getElementById(nameId);
      var errEl = document.getElementById(errorId);
      input.addEventListener('change', function() {
        if (this.files && this.files[0]) {
          area.classList.add('has-file');
          nameEl.textContent = this.files[0].name;
          errEl.style.display = 'none';
        } else {
          area.classList.remove('has-file');
          nameEl.textContent = '';
        }
      });
      ['dragover','dragenter'].forEach(function(ev){
        area.addEventListener(ev, function(e){e.preventDefault();area.classList.add('dragover');});
      });
      ['dragleave','drop'].forEach(function(ev){
        area.addEventListener(ev, function(){area.classList.remove('dragover');});
      });
    }
    setupUpload('cfUpload','cfFile','cfFileName','cfError');
    setupUpload('lcUpload','lcFile','lcFileName','lcError');
    function validateSetup() {
      var ok = true;
      var cf = document.getElementById('cfHandle');
      var lc = document.getElementById('lcUser');
      var cfF = document.getElementById('cfFile');
      var lcF = document.getElementById('lcFile');
      [cf,lc].forEach(function(el){el.classList.remove('invalid');});
      ['cfError','lcError'].forEach(function(id){document.getElementById(id).style.display='none';});
      if (!cf.value.trim()) { cf.classList.add('invalid'); ok = false; }
      if (!lc.value.trim()) { lc.classList.add('invalid'); ok = false; }
      if (!cfF.files || !cfF.files.length) {
        document.getElementById('cfError').style.display = 'block'; ok = false;
      }
      if (!lcF.files || !lcF.files.length) {
        document.getElementById('lcError').style.display = 'block'; ok = false;
      }
      return ok;
    }
  </script>
</body>
</html>"""


# --- Dashboard HTML (placeholders: __PROFILE_CARDS_HTML__, __CONTEST_LIST_HTML__) ---
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <title>CP Assistant — Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg:#0c0f17;--surface:#151922;--surface2:#1c212c;--border:#2a3142;
      --text:#e6e9f0;--text-muted:#8b92a5;--accent:#6366f1;--accent-hover:#4f46e5;
      --success:#22c55e;--danger:#ef4444;--warning:#f59e0b;
      --cf-gray:#94a3b8;--cf-green:#22c55e;--cf-cyan:#22d3ee;--cf-blue:#3b82f6;
      --cf-violet:#a78bfa;--cf-orange:#f97316;--cf-red:#ef4444;
    }
    *{box-sizing:border-box;}
    body{font-family:'DM Sans',system-ui,sans-serif;margin:0;padding:0;background:var(--bg);color:var(--text);line-height:1.6;min-height:100vh;}
    .app{display:flex;min-height:100vh;max-width:1200px;margin:0 auto;}
    .sidebar{width:260px;flex-shrink:0;padding:28px 20px;background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;}
    .logo{font-size:1.35rem;font-weight:700;color:var(--text);letter-spacing:-0.02em;margin-bottom:28px;}
    .nav{display:flex;flex-direction:column;gap:4px;}
    .nav a{display:flex;align-items:center;padding:12px 16px;color:var(--text-muted);text-decoration:none;border-radius:10px;font-weight:500;transition:background .15s,color .15s;font-size:0.9rem;}
    .nav a:hover{background:var(--surface2);color:var(--text);}
    .nav a.active{background:var(--accent);color:#fff;}
    .sidebar-bottom{margin-top:auto;}
    .btn-update{width:100%;margin-top:20px;padding:14px;background:var(--success);color:#fff;border:none;border-radius:10px;cursor:pointer;font-weight:600;font-size:0.95rem;font-family:inherit;transition:filter .15s;}
    .btn-update:hover{filter:brightness(1.1);}
    .btn-update:disabled{opacity:0.7;cursor:not-allowed;}
    .sidebar-hint{font-size:0.78rem;color:var(--text-muted);margin-top:12px;line-height:1.4;}
    .settings-link{display:block;margin-top:12px;font-size:0.85rem;color:var(--accent);text-decoration:none;}
    .settings-link:hover{text-decoration:underline;}
    .main{flex:1;padding:36px 40px;overflow-x:hidden;}
    .page{display:none;}
    .page.active{display:block;animation:fadeIn .2s ease;}
    @keyframes fadeIn{from{opacity:0;}to{opacity:1;}}
    .page-title{font-size:1.75rem;font-weight:700;margin:0 0 28px 0;color:var(--text);letter-spacing:-0.02em;}
    .section-label{font-size:0.8rem;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:12px;display:block;}
    .profile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:20px;}
    .profile-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px;transition:border-color .15s;}
    .profile-card:hover{border-color:var(--accent);}
    .profile-card-header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:16px;}
    .profile-card-badge{font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:var(--text-muted);}
    .profile-link{color:var(--accent);text-decoration:none;font-weight:600;}
    .profile-link:hover{text-decoration:underline;}
    .profile-stats{display:flex;flex-direction:column;gap:10px;}
    .stat-row{display:flex;justify-content:space-between;align-items:center;font-size:0.9rem;}
    .stat-label{color:var(--text-muted);}
    .stat-value{font-weight:600;color:var(--text);}
    .cf-rating{font-weight:700;font-size:1.1rem;}
    .cf-gray{color:var(--cf-gray);}.cf-green{color:var(--cf-green);}.cf-cyan{color:var(--cf-cyan);}.cf-blue{color:var(--cf-blue);}.cf-violet{color:var(--cf-violet);}.cf-orange{color:var(--cf-orange);}.cf-red{color:var(--cf-red);}
    .card-block{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:24px;margin-top:24px;}
    .contest-list{display:flex;flex-direction:column;gap:10px;}
    .contest-row{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;padding:16px;background:var(--surface2);border-radius:10px;border:1px solid var(--border);}
    .contest-name{font-weight:600;color:var(--text);}
    .contest-meta{font-size:0.85rem;color:var(--text-muted);margin-top:4px;}
    .btn{padding:10px 18px;border-radius:8px;border:none;cursor:pointer;font-weight:500;font-size:0.9rem;font-family:inherit;transition:background .15s;}
    .btn-register{background:var(--accent);color:#fff;}
    .btn-register:hover{background:var(--accent-hover);}
    .btn-outline{background:transparent;color:var(--accent);border:1px solid var(--accent);text-decoration:none;}
    .btn-outline:hover{background:var(--accent);color:#fff;}
    .contest-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
    .btn-refresh{background:var(--surface2);color:var(--text);border:1px solid var(--border);margin-top:16px;}
    .btn-refresh:hover{background:var(--border);}
    .card-message{padding:20px;text-align:center;border-radius:10px;font-size:0.95rem;}
    .card-message-empty{color:var(--text-muted);background:var(--surface2);}
    .card-message-err{color:var(--danger);background:rgba(239,68,68,0.08);}
    .tag-list{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0;}
    .tag{padding:6px 12px;background:var(--surface2);border-radius:8px;font-size:0.85rem;}
    .tag.weak{border-left:3px solid var(--danger);}
    .tag.strong{border-left:3px solid var(--success);}
    .plan-list{list-style:none;padding:0;margin:0;}
    .plan-list li{padding:12px 0;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;}
    .plan-list li:last-child{border-bottom:none;}
    .err{color:var(--danger);}
    .hint{font-size:0.8rem;color:var(--text-muted);margin-top:12px;}
    /* Problem cards */
    .problem-grid{display:flex;flex-direction:column;gap:10px;margin-top:12px;}
    .problem-card{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:14px 16px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;transition:border-color .15s;}
    .problem-card:hover{border-color:var(--accent);}
    .problem-card a{color:var(--accent);text-decoration:none;font-weight:600;font-size:0.9rem;}
    .problem-card a:hover{text-decoration:underline;}
    .problem-diff{font-size:0.75rem;font-weight:600;padding:4px 10px;border-radius:6px;text-transform:uppercase;}
    .diff-easy,.diff-800,.diff-900,.diff-1000,.diff-1100{background:rgba(34,197,94,0.15);color:var(--success);}
    .diff-medium,.diff-1200,.diff-1300,.diff-1400,.diff-1500,.diff-1600{background:rgba(245,158,11,0.15);color:var(--warning);}
    .diff-hard,.diff-1700,.diff-1800,.diff-1900,.diff-2000,.diff-2100,.diff-2200,.diff-2300,.diff-2400,.diff-2500{background:rgba(239,68,68,0.15);color:var(--danger);}
    .problem-tags{display:flex;flex-wrap:wrap;gap:4px;}
    .problem-tags span{font-size:0.7rem;padding:2px 8px;background:var(--bg);border-radius:4px;color:var(--text-muted);}
    /* Registration status */
    .reg-status{display:inline-block;font-size:0.75rem;font-weight:600;padding:4px 10px;border-radius:6px;text-transform:uppercase;}
    .reg-success{background:rgba(34,197,94,0.15);color:var(--success);}
    .reg-failed{background:rgba(239,68,68,0.15);color:var(--danger);}
    .reg-pending{background:rgba(245,158,11,0.15);color:var(--warning);}
    /* Analytics bar chart */
    .bar-chart{display:flex;flex-direction:column;gap:6px;margin:12px 0;}
    .bar-row{display:flex;align-items:center;gap:10px;}
    .bar-label{font-size:0.8rem;color:var(--text-muted);min-width:120px;text-align:right;}
    .bar-track{flex:1;height:20px;background:var(--surface2);border-radius:4px;overflow:hidden;position:relative;}
    .bar-fill{height:100%;border-radius:4px;transition:width .3s ease;}
    .bar-fill-strong{background:var(--success);}
    .bar-fill-weak{background:var(--danger);}
    .bar-fill-neutral{background:var(--accent);}
    .bar-count{font-size:0.75rem;color:var(--text-muted);min-width:30px;}
    /* Floating chat widget */
    .chat-fab{position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:var(--accent);color:#fff;border:none;cursor:pointer;font-size:1.5rem;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(0,0,0,0.4);z-index:1000;transition:transform .15s;}
    .chat-fab:hover{transform:scale(1.08);}
    .chat-panel{position:fixed;bottom:90px;right:24px;width:380px;max-height:520px;background:var(--surface);border:1px solid var(--border);border-radius:16px;display:none;flex-direction:column;z-index:1000;box-shadow:0 8px 32px rgba(0,0,0,0.5);overflow:hidden;}
    .chat-panel.open{display:flex;}
    .chat-header{padding:14px 18px;background:var(--accent);color:#fff;font-weight:600;display:flex;align-items:center;justify-content:space-between;font-size:0.95rem;}
    .chat-header-right{display:flex;align-items:center;gap:10px;}
    .solution-toggle{display:flex;align-items:center;gap:6px;font-size:0.75rem;font-weight:500;}
    .solution-toggle input{accent-color:var(--danger);}
    .chat-close{background:none;border:none;color:#fff;font-size:1.2rem;cursor:pointer;padding:0;line-height:1;}
    .chat-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;min-height:200px;max-height:360px;}
    .chat-msg{max-width:85%;padding:10px 14px;border-radius:12px;font-size:0.85rem;line-height:1.5;word-wrap:break-word;}
    .chat-msg-user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px;}
    .chat-msg-bot{align-self:flex-start;background:var(--surface2);color:var(--text);border-bottom-left-radius:4px;}
    .chat-msg-bot pre{background:var(--bg);padding:8px;border-radius:6px;overflow-x:auto;font-size:0.8rem;margin:6px 0;}
    .chat-msg-bot code{font-size:0.8rem;}
    .chat-input-row{display:flex;gap:8px;padding:12px;border-top:1px solid var(--border);background:var(--surface);}
    .chat-input{flex:1;padding:10px 14px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:inherit;font-size:0.85rem;resize:none;}
    .chat-input:focus{outline:none;border-color:var(--accent);}
    .chat-send{padding:10px 16px;background:var(--accent);color:#fff;border:none;border-radius:8px;cursor:pointer;font-weight:600;font-family:inherit;font-size:0.85rem;}
    .chat-send:hover{background:var(--accent-hover);}
    .chat-send:disabled{opacity:0.6;cursor:not-allowed;}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="logo">CP Assistant</div>
      <nav class="nav">
        <a href="#" class="active" data-page="overview">Overview</a>
        <a href="#" data-page="practice">Practice</a>
        <a href="#" data-page="registrations">Registered</a>
        <a href="#" data-page="analytics">Insights</a>
        <a href="#" data-page="plan">Training Plan</a>
      </nav>
      <div class="sidebar-bottom">
        <button class="btn-update" type="button" onclick="updateData()">Update Data</button>
        <p class="sidebar-hint">Sync practice and contests from Codeforces &amp; LeetCode.</p>
        <a href="/setup" class="settings-link">Settings</a>
      </div>
    </aside>
    <main class="main">
      <!-- Overview -->
      <section id="overview" class="page active">
        <h1 class="page-title">Overview</h1>
        <span class="section-label">Your profiles</span>
        __PROFILE_CARDS_HTML__
        <div class="card-block">
          <span class="section-label">Upcoming contests</span>
          __CONTEST_LIST_HTML__
          <a href="/api/refresh-overview" class="btn btn-refresh" style="display:inline-block;text-decoration:none;">Refresh overview</a>
          <p class="hint">Register (auto) uses your imported cookies so the app never needs your password.</p>
        </div>
        <div class="card-block" id="session-block">
          <span class="section-label">Cookie Status</span>
          <div id="session-status" style="margin:8px 0;font-size:0.9rem;">Loading...</div>
          <p class="hint">Cookies were imported during setup. To update them, go to <a href="/setup" style="color:var(--accent);font-weight:600;">Settings</a> and re-upload.</p>
        </div>
      </section>
      <!-- Practice -->
      <section id="practice" class="page">
        <h1 class="page-title">Practice</h1>
        <div class="card-block">
          <span class="section-label">Last 30 days</span>
          <div id="practice-content" class="card-message card-message-empty">Loading...</div>
        </div>
        <div class="card-block">
          <span class="section-label">Recommended Problems</span>
          <div id="recommended-problems" class="card-message card-message-empty">Loading...</div>
        </div>
      </section>
      <!-- Registered Contests -->
      <section id="registrations" class="page">
        <h1 class="page-title">Registered Contests</h1>
        <div class="card-block">
          <div id="registrations-content" class="card-message card-message-empty">Loading...</div>
        </div>
      </section>
      <!-- Insights / Analytics -->
      <section id="analytics" class="page">
        <h1 class="page-title">Insights</h1>
        <div class="card-block">
          <span class="section-label">Weak &amp; Strong Tags</span>
          <div id="analytics-tags" class="card-message card-message-empty">Loading...</div>
        </div>
        <div class="card-block">
          <span class="section-label">Tag Distribution</span>
          <div id="analytics-chart" class="card-message card-message-empty">Loading...</div>
        </div>
        <div class="card-block">
          <span class="section-label">Rating Trend</span>
          <div id="analytics-rating" class="card-message card-message-empty">Loading...</div>
        </div>
      </section>
      <!-- Training Plan -->
      <section id="plan" class="page">
        <h1 class="page-title">Training Plan</h1>
        <div class="card-block">
          <span class="section-label">Recommended today</span>
          <div id="plan-content" class="card-message card-message-empty">Loading...</div>
        </div>
      </section>
    </main>
  </div>
  <!-- Floating Chat Widget -->
  <button class="chat-fab" onclick="toggleChat()" title="AI Tutor">&#128172;</button>
  <div class="chat-panel" id="chatPanel">
    <div class="chat-header">
      <span>CP Tutor</span>
      <div class="chat-header-right">
        <label class="solution-toggle"><input type="checkbox" id="solutionToggle"> Solution</label>
        <button class="chat-close" onclick="toggleChat()">&times;</button>
      </div>
    </div>
    <div class="chat-messages" id="chatMessages">
      <div class="chat-msg chat-msg-bot">Hi! I'm your competitive programming tutor. Ask me about any problem and I'll guide you through it. Turn on <b>Solution</b> mode for full answers.</div>
    </div>
    <div class="chat-input-row">
      <input class="chat-input" id="chatInput" placeholder="Ask about a problem..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChat();}">
      <button class="chat-send" id="chatSend" onclick="sendChat()">Send</button>
    </div>
  </div>
  <script>
    var chatHistory = [];
    function toggleChat() {
      var p = document.getElementById('chatPanel');
      p.classList.toggle('open');
      if (p.classList.contains('open')) document.getElementById('chatInput').focus();
    }
    function sendChat() {
      var input = document.getElementById('chatInput');
      var msg = input.value.trim();
      if (!msg) return;
      input.value = '';
      var msgs = document.getElementById('chatMessages');
      msgs.innerHTML += '<div class="chat-msg chat-msg-user">' + escHtml(msg) + '</div>';
      msgs.scrollTop = msgs.scrollHeight;
      chatHistory.push({role:'user',content:msg});
      var btn = document.getElementById('chatSend');
      btn.disabled = true;
      btn.textContent = '...';
      var showSol = document.getElementById('solutionToggle').checked;
      fetch('/api/chat', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({message:msg,history:chatHistory,show_solution:showSol})
      }).then(function(r){return r.json();}).then(function(d){
        var reply = d.reply || 'No response.';
        chatHistory.push({role:'assistant',content:reply});
        msgs.innerHTML += '<div class="chat-msg chat-msg-bot">' + formatReply(reply) + '</div>';
        msgs.scrollTop = msgs.scrollHeight;
      }).catch(function(e){
        msgs.innerHTML += '<div class="chat-msg chat-msg-bot" style="color:var(--danger)">Error: '+escHtml(e.message)+'</div>';
      }).finally(function(){btn.disabled=false;btn.textContent='Send';});
    }
    function escHtml(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
    function formatReply(s){
      // Simple markdown: **bold**, `code`, ```blocks```, newlines
      s = escHtml(s);
      s = s.replace(/```([\\s\\S]*?)```/g,'<pre><code>$1</code></pre>');
      s = s.replace(/`([^`]+)`/g,'<code>$1</code>');
      s = s.replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');
      s = s.replace(/\\n/g,'<br>');
      return s;
    }
    function show(pageId) {
      document.querySelectorAll('.page').forEach(function(p){p.classList.remove('active');});
      document.querySelectorAll('.nav a').forEach(function(a){a.classList.remove('active');});
      var page = document.getElementById(pageId);
      if(page) page.classList.add('active');
      var link = document.querySelector('.nav a[data-page="'+pageId+'"]');
      if(link) link.classList.add('active');
      if(pageId==='practice'){loadPractice();loadRecommended();}
      if(pageId==='analytics') loadAnalytics();
      if(pageId==='plan') loadPlan();
      if(pageId==='registrations') loadRegistrations();
    }
    document.querySelectorAll('.nav a').forEach(function(a){
      a.addEventListener('click',function(e){e.preventDefault();show(this.getAttribute('data-page'));});
    });
    function register(platform,contestId){
      if(!platform||!contestId){alert('Missing platform or contest id');return;}
      fetch('/api/register?platform='+encodeURIComponent(platform)+'&contest_id='+encodeURIComponent(contestId),{method:'POST'})
        .then(function(r){return r.json().catch(function(){return {success:false,message:r.status+' '+r.statusText};});})
        .then(function(d){alert(d.message||(d.success?'Done':'Failed'));})
        .catch(function(e){alert('Error: '+e.message);});
    }
    function loadPractice(){
      fetch('/api/practice/summary?days=30').then(function(r){return r.json();}).then(function(d){
        var total=d.total||0;var by=d.by_platform||{};
        var byStr=Object.keys(by).length?Object.entries(by).map(function(kv){return kv[0]+': '+kv[1];}).join(' / '):'--';
        document.getElementById('practice-content').innerHTML='<div class="stat-row"><span class="stat-label">Solved (30d)</span><span class="stat-value">'+total+'</span></div><div class="stat-row"><span class="stat-label">By platform</span><span class="stat-value">'+byStr+'</span></div>'+(d.source?'<p class="hint">'+d.source+'</p>':'');
      }).catch(function(){document.getElementById('practice-content').innerHTML='<span class="err">Could not load</span>';});
    }
    function loadRecommended(){
      var el=document.getElementById('recommended-problems');
      el.innerHTML='<div class="card-message card-message-empty">Loading problems...</div>';
      fetch('/api/practice/recommended').then(function(r){return r.json();}).then(function(problems){
        if(!problems||!problems.length){el.innerHTML='<div class="card-message card-message-empty">No recommendations yet. Click Update Data first.</div>';return;}
        var html='<div class="problem-grid">';
        problems.forEach(function(p){
          var diffClass='diff-'+p.difficulty.toLowerCase().replace(/[^a-z0-9]/g,'');
          var tags=p.tags?p.tags.slice(0,3).map(function(t){return '<span>'+escHtml(t)+'</span>';}).join(''):'';
          html+='<div class="problem-card"><div><a href="'+escHtml(p.url)+'" target="_blank">'+escHtml(p.name)+'</a>'+(tags?'<div class="problem-tags" style="margin-top:4px;">'+tags+'</div>':'')+'</div><div style="display:flex;align-items:center;gap:8px;"><span class="problem-diff '+diffClass+'">'+escHtml(p.difficulty)+'</span><span style="font-size:0.75rem;color:var(--text-muted);text-transform:uppercase;">'+escHtml(p.platform)+'</span></div></div>';
        });
        html+='</div>';
        el.innerHTML=html;
      }).catch(function(){el.innerHTML='<span class="err">Could not load</span>';});
    }
    function loadRegistrations(){
      var el=document.getElementById('registrations-content');
      el.innerHTML='<div class="card-message card-message-empty">Loading...</div>';
      fetch('/api/registrations').then(function(r){return r.json();}).then(function(regs){
        if(!regs||!regs.length){el.innerHTML='<div class="card-message card-message-empty">No contest registrations yet. Register for a contest from the Overview tab.</div>';return;}
        var html='<div class="contest-list">';
        regs.forEach(function(r){
          var statusClass='reg-'+(r.status||'pending');
          var name=r.contest_name||r.contest_id||'Unknown';
          var platform=r.platform||'';
          var ts=r.start_time_utc?new Date(r.start_time_utc*1000).toLocaleString():'';
          var created=r.created_at?new Date(r.created_at*1000).toLocaleString():'';
          html+='<div class="contest-row"><div class="contest-info"><div class="contest-name">'+escHtml(name)+'</div><div class="contest-meta">'+escHtml(platform)+(ts?' / '+ts:'')+(created?' / registered '+created:'')+'</div></div><span class="reg-status '+statusClass+'">'+(r.status||'pending')+'</span></div>';
        });
        html+='</div>';
        el.innerHTML=html;
      }).catch(function(){el.innerHTML='<span class="err">Could not load</span>';});
    }
    function loadAnalytics(){
      // Tags
      fetch('/api/analytics/weak-strong-tags').then(function(r){return r.json();}).then(function(d){
        var weak=(d.weak_tags||[]).length?'<div class="section-label" style="margin-top:8px;">Weak (need practice)</div><div class="tag-list">'+(d.weak_tags||[]).map(function(t){return '<span class="tag weak">'+escHtml(t)+'</span>';}).join('')+'</div>':'';
        var strong=(d.strong_tags||[]).length?'<div class="section-label" style="margin-top:12px;">Strong</div><div class="tag-list">'+(d.strong_tags||[]).map(function(t){return '<span class="tag strong">'+escHtml(t)+'</span>';}).join('')+'</div>':'';
        document.getElementById('analytics-tags').innerHTML=(weak||strong)?weak+strong+'<div class="stat-row" style="margin-top:16px;"><span class="stat-label">Total solved</span><span class="stat-value">'+(d.total_solved||0)+'</span></div>':'<div class="card-message card-message-empty">No tag data. Click Update Data.</div>';
        // Bar chart
        var counts=d.tag_counts||{};
        var sorted=Object.entries(counts).sort(function(a,b){return b[1]-a[1];}).slice(0,15);
        if(sorted.length){
          var max=sorted[0][1]||1;
          var chartHtml='<div class="bar-chart">';
          var weakSet=new Set(d.weak_tags||[]);
          var strongSet=new Set(d.strong_tags||[]);
          sorted.forEach(function(kv){
            var pct=Math.round(kv[1]/max*100);
            var cls=weakSet.has(kv[0])?'bar-fill-weak':strongSet.has(kv[0])?'bar-fill-strong':'bar-fill-neutral';
            chartHtml+='<div class="bar-row"><span class="bar-label">'+escHtml(kv[0])+'</span><div class="bar-track"><div class="bar-fill '+cls+'" style="width:'+pct+'%"></div></div><span class="bar-count">'+kv[1]+'</span></div>';
          });
          chartHtml+='</div>';
          document.getElementById('analytics-chart').innerHTML=chartHtml;
        } else {
          document.getElementById('analytics-chart').innerHTML='<div class="card-message card-message-empty">No data yet.</div>';
        }
      }).catch(function(){
        document.getElementById('analytics-tags').innerHTML='<span class="err">Could not load</span>';
        document.getElementById('analytics-chart').innerHTML='';
      });
      // Rating trend
      fetch('/api/rating-history?limit=20').then(function(r){return r.json();}).then(function(history){
        if(!history||!history.length){document.getElementById('analytics-rating').innerHTML='<div class="card-message card-message-empty">No rating data. Click Update Data.</div>';return;}
        var html='<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:0.85rem;"><tr style="border-bottom:1px solid var(--border);"><th style="text-align:left;padding:8px;color:var(--text-muted);">Platform</th><th style="text-align:left;padding:8px;color:var(--text-muted);">Contest</th><th style="text-align:right;padding:8px;color:var(--text-muted);">Old</th><th style="text-align:right;padding:8px;color:var(--text-muted);">New</th><th style="text-align:right;padding:8px;color:var(--text-muted);">Change</th></tr>';
        history.reverse().forEach(function(r){
          var change=r.new_rating-r.old_rating;
          var color=change>0?'var(--success)':change<0?'var(--danger)':'var(--text-muted)';
          var sign=change>0?'+':'';
          html+='<tr style="border-bottom:1px solid var(--border);"><td style="padding:8px;">'+escHtml(r.platform||'')+'</td><td style="padding:8px;">'+escHtml(r.contest_id||'')+'</td><td style="text-align:right;padding:8px;">'+r.old_rating+'</td><td style="text-align:right;padding:8px;font-weight:600;">'+r.new_rating+'</td><td style="text-align:right;padding:8px;color:'+color+';font-weight:600;">'+sign+change+'</td></tr>';
        });
        html+='</table></div>';
        document.getElementById('analytics-rating').innerHTML=html;
      }).catch(function(){document.getElementById('analytics-rating').innerHTML='<span class="err">Could not load</span>';});
    }
    function loadPlan(){
      fetch('/api/analytics/training-plan').then(function(r){return r.json();}).then(function(d){
        var items=d.problems_today||[];
        document.getElementById('plan-content').innerHTML=items.length?'<ul class="plan-list">'+items.map(function(i){return '<li>'+escHtml(i)+'</li>';}).join('')+'</ul>':'<div class="card-message card-message-empty">Sync practice first (Update Data).</div>';
      }).catch(function(){document.getElementById('plan-content').innerHTML='<span class="err">Could not load</span>';});
    }
    function updateData(){
      var btn=document.querySelector('.btn-update');
      if(!btn)return;
      btn.textContent='Syncing...';btn.disabled=true;
      fetch('/api/update-data',{method:'POST'}).then(function(r){return r.json().catch(function(){return {};});}).then(function(d){
        alert(d.message||'Done');if(d.status==='ok')window.location.reload();
      }).catch(function(e){alert(e.message||'Request failed');}).finally(function(){btn.textContent='Update Data';btn.disabled=false;});
    }
    function loadSessionStatus(){
      var el=document.getElementById('session-status');if(!el)return;
      fetch('/api/session/status').then(function(r){return r.json();}).then(function(d){
        var cf=d.codeforces?'&#9989; Codeforces':'&#10060; Codeforces';
        var lc=d.leetcode?'&#9989; LeetCode':'&#10060; LeetCode';
        el.innerHTML=cf+' &nbsp; '+lc;
      }).catch(function(){el.textContent='Could not load';});
    }
    loadSessionStatus();
  </script>
</body>
</html>"""


def _get_cached_overview() -> tuple[dict, list]:
    """Return (profile, contests) from cache. Caller holds lock or accepts copy."""
    with _overview_cache_lock:
        return (
            dict(_overview_cache.get("profile") or {"codeforces": {}, "leetcode": {}}),
            list(_overview_cache.get("contests") or []),
        )


@app.get("/api/refresh-overview", response_class=RedirectResponse)
def api_refresh_overview():
    """Refresh overview cache then redirect to dashboard. Use for 'Refresh page' link."""
    _refresh_overview_cache()
    return RedirectResponse(url="/", status_code=302)


def _needs_setup() -> bool:
    """Return True if user hasn't completed setup (no handles + cookies in DB)."""
    try:
        from db.dal import get_user_config, get_browser_cookies
        config = get_user_config("default")
        if not config:
            return True
        cf = config.get("codeforces_handle", "")
        lc = config.get("leetcode_username", "")
        if not cf or not lc:
            return True
        # Also require at least one set of cookies
        has_cf_cookies = get_browser_cookies("default", "codeforces") is not None
        has_lc_cookies = get_browser_cookies("default", "leetcode") is not None
        if not has_cf_cookies or not has_lc_cookies:
            return True
        return False
    except Exception:
        # DB might be down; check .env fallback
        cf = settings.CODEFORCES_HANDLE
        lc = settings.LEETCODE_USERNAME
        if cf and cf not in ("your_cf_handle", "") and lc and lc not in ("your_lc_username", ""):
            # Check cookie fallback files
            try:
                from utils.cookie_fallback import load_cookies_fallback
                if load_cookies_fallback("codeforces") and load_cookies_fallback("leetcode"):
                    return False
            except Exception:
                pass
        return True


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve dashboard from cache. If no config, redirect to setup. If cache empty, show loading."""
    # Check if user needs to set up first
    if _needs_setup():
        return RedirectResponse(url="/setup", status_code=302)

    profile, contests = _get_cached_overview()
    has_data = (
        (profile.get("codeforces") or profile.get("leetcode")) or len(contests) > 0
    )
    if not has_data:
        return HTMLResponse(
            LOADING_HTML,
            headers={"Cache-Control": "no-store"},
        )
    profile_html = _build_profile_cards_html(profile)
    contest_html = _build_contest_list_html(contests)
    html = (
        DASHBOARD_HTML.replace("__PROFILE_CARDS_HTML__", profile_html)
        .replace("__CONTEST_LIST_HTML__", contest_html)
    )
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
