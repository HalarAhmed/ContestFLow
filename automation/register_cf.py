"""Codeforces contest registration via Playwright.

Prefer stored cookies (pasted in dashboard) so we skip login and avoid Cloudflare.
Else use persistent browser session (login_once.py) or fresh login with credentials.
"""
import time

from config import settings
from utils.logging import get_logger
from automation.browser_session import get_session_dir, has_session
from automation.stealth import launch_persistent_context, launch_options, STEALTH_SCRIPT

logger = get_logger(__name__)


def register_codeforces(contest_id: str, username: str, password: str, headless: bool | None = None) -> tuple[bool, str]:
    """
    Register for a Codeforces contest. Returns (success, message).
    contest_id: e.g. "2200" from contest list.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright not installed. Run: pip install playwright && playwright install chromium"

    if headless is None:
        headless = getattr(settings, "REGISTER_HEADLESS", False)

    reg_url = f"https://codeforces.com/contestRegistration/{contest_id}"

    # 1) Stored cookies (pasted in dashboard or local file when MongoDB is down)
    stored_cookies = None
    try:
        from db.dal import get_browser_cookies
        stored_cookies = get_browser_cookies("default", "codeforces")
    except Exception:
        pass
    if not stored_cookies:
        try:
            from utils.cookie_fallback import load_cookies_fallback
            stored_cookies = load_cookies_fallback("codeforces")
        except Exception:
            pass

    with sync_playwright() as p:
        browser = None
        use_persistent = False
        use_stored_cookies = bool(stored_cookies)

        if use_stored_cookies:
            logger.info("Using stored Codeforces cookies from dashboard")
            try:
                browser = p.chromium.launch(headless=headless, channel="chrome", **launch_options())
            except Exception:
                browser = p.chromium.launch(headless=headless, **launch_options())
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
            )
            context.add_init_script(STEALTH_SCRIPT)
            # Add cookies before navigating so we're "logged in"
            context.add_cookies(stored_cookies)
            page = context.new_page()
        elif has_session("codeforces"):
            use_persistent = True
            session_dir = get_session_dir("codeforces")
            logger.info("Using saved Codeforces session from %s", session_dir)
            context = launch_persistent_context(p, session_dir, headless=headless)
            context.add_init_script(STEALTH_SCRIPT)
            page = context.pages[0] if context.pages else context.new_page()
        else:
            if not username or not password:
                return False, "No cookies and no credentials. Import cookies in the dashboard or run: python login_once.py codeforces"
            logger.info("No saved session; using fresh login for Codeforces")
            try:
                browser = p.chromium.launch(headless=headless, channel="chrome", **launch_options())
            except Exception:
                browser = p.chromium.launch(headless=headless, **launch_options())
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US",
            )
            context.add_init_script(STEALTH_SCRIPT)
            page = context.new_page()

        try:
            page.goto(reg_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)

            # Check if we need to log in (redirected to /enter)
            if "enter" in page.url.lower() or "login" in page.url.lower():
                if use_stored_cookies:
                    _close(context, browser)
                    return False, "Stored cookies expired. Re-import cookies from the dashboard (log in in your browser and paste again)."
                if use_persistent:
                    context.close()
                    return False, "Saved session expired. Run: python login_once.py codeforces or import cookies in the dashboard."

                # Fresh login flow
                login_url = "https://codeforces.com/enter"
                handle_selector = (
                    'input[name="handleOrEmail"], input[name="handle"], '
                    'input[id="handleOrEmail"], input[id="handle"], '
                    'input[placeholder*="Handle" i], input[placeholder*="Email" i]'
                )
                body_lower = page.content().lower()
                cloudflare_challenge = (
                    "verify you are human" in body_lower
                    or page.locator('input[name="cf-turnstile-response"]').count() > 0
                )
                form_timeout = 45_000
                if cloudflare_challenge:
                    if headless:
                        _close(context, browser)
                        return False, "Cloudflare challenge detected. Run: python login_once.py codeforces"
                    logger.info("Cloudflare challenge detected. Complete it in the browser (2 min).")
                    form_timeout = 120_000
                try:
                    page.wait_for_selector(handle_selector, state="visible", timeout=form_timeout)
                except Exception:
                    page.goto(login_url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(3)
                    page.wait_for_selector(handle_selector, state="visible", timeout=45000)
                page.locator(handle_selector).first.fill(username)
                page.locator('input[name="password"], input[type="password"]').first.fill(password)
                page.locator('input[type="submit"], button[type="submit"]').first.click()
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                if "enter" in page.url.lower():
                    _close(context, browser)
                    return False, "Login failed: check handle and password"
                page.goto(reg_url, wait_until="domcontentloaded", timeout=25000)
                time.sleep(1)

            # Registration page: detect state from body (and toast text)
            body = page.content()
            body_lower = body.lower()

            # Already registered (page or contests list shows "Registration completed")
            if (
                "already registered" in body_lower
                or "you have been successfully registered" in body_lower
                or "registration completed" in body_lower
            ):
                _close(context, browser)
                return True, "Already registered or registration confirmed"

            # No registration open (toast: "No registration is opened now" or similar on page)
            if "no registration is opened now" in body_lower or "no registration is open" in body_lower:
                _close(context, browser)
                return False, "No registration is open for this contest right now."

            # Registration not started yet ("Before registration X days/minutes")
            if "before registration" in body_lower or "registration will open" in body_lower:
                _close(context, browser)
                return False, "Registration has not opened yet for this contest."

            submit_locator = page.locator('input[type="submit"]')
            if submit_locator.count() > 0:
                submit_locator.first.click()
                page.wait_for_load_state("domcontentloaded", timeout=12000)
                body = page.content()
                body_lower = body.lower()
                if "successfully registered" in body_lower or "already registered" in body_lower or "registration completed" in body_lower:
                    _close(context, browser)
                    return True, "Registered successfully"
                # Only treat as closed when Codeforces clearly says so
                if (
                    "registration is closed" in body_lower
                    or "registration has been closed" in body_lower
                    or "registration was closed" in body_lower
                ):
                    _close(context, browser)
                    return False, "Registration is closed"

            _close(context, browser)
            return False, "No registration is open for this contest right now."
        except Exception as e:
            _close(context, browser)
            logger.exception("Codeforces registration error: %s", e)
            err = str(e)
            if "element is not enabled" in err or "not enabled" in err.lower():
                return False, "A button/field was not ready. Try: python login_once.py codeforces"
            if len(err) > 200:
                err = err[:200] + "..."
            return False, err


def _close(context, browser):
    """Safely close context and browser."""
    try:
        context.close()
    except Exception:
        pass
    if browser:
        try:
            browser.close()
        except Exception:
            pass
