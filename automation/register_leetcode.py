"""LeetCode contest registration via Playwright.

Prefer stored cookies (pasted in dashboard) so we skip login and avoid Cloudflare.
Else use persistent browser session (login_once.py) or fresh login with credentials.
"""
import time

from config import settings
from utils.logging import get_logger
from automation.browser_session import get_session_dir, has_session
from automation.stealth import launch_persistent_context, launch_options, STEALTH_SCRIPT

logger = get_logger(__name__)


def register_leetcode(contest_slug: str, username: str, password: str, headless: bool | None = None) -> tuple[bool, str]:
    """
    Register for a LeetCode contest. Returns (success, message).
    contest_slug: e.g. "weekly-contest-490" from contests/upcoming.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright not installed. Run: pip install playwright && playwright install chromium"

    if headless is None:
        headless = getattr(settings, "REGISTER_HEADLESS", False)

    contest_url = f"https://leetcode.com/contest/{contest_slug}/"

    # 1) Stored cookies (pasted in dashboard or local file when MongoDB is down)
    stored_cookies = None
    try:
        from db.dal import get_browser_cookies
        stored_cookies = get_browser_cookies("default", "leetcode")
    except Exception:
        pass
    if not stored_cookies:
        try:
            from utils.cookie_fallback import load_cookies_fallback
            stored_cookies = load_cookies_fallback("leetcode")
        except Exception:
            pass

    with sync_playwright() as p:
        browser = None
        use_persistent = False
        use_stored_cookies = bool(stored_cookies)

        if use_stored_cookies:
            logger.info("Using stored LeetCode cookies from dashboard")
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
            context.add_cookies(stored_cookies)
            page = context.new_page()
        elif has_session("leetcode"):
            use_persistent = True
            session_dir = get_session_dir("leetcode")
            logger.info("Using saved LeetCode session from %s", session_dir)
            context = launch_persistent_context(p, session_dir, headless=headless)
            context.add_init_script(STEALTH_SCRIPT)
            page = context.pages[0] if context.pages else context.new_page()
        else:
            if not username or not password:
                return False, "No cookies and no credentials. Import cookies in the dashboard or run: python login_once.py leetcode"
            logger.info("No saved session; using fresh login for LeetCode")
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
            page.goto(contest_url, wait_until="domcontentloaded", timeout=45000)
            # LeetCode is a Next.js SPA; wait for React hydration
            time.sleep(4)

            # Check if redirected to login
            if "/accounts/login" in page.url.lower() or "/login" in page.url.lower():
                if use_stored_cookies:
                    _close(context, browser)
                    return False, "Stored cookies expired. Re-import cookies from Settings (log in in your browser and re-export)."
                if use_persistent:
                    context.close()
                    return False, "Saved session expired. Run: python login_once.py leetcode or re-import cookies."

                # Fresh login flow
                login_selector = (
                    'input[name="login"], input#id_login, '
                    'input[placeholder*="e-mail" i], input[placeholder*="username" i], '
                    'input[type="email"], input[aria-label*="login" i], input[aria-label*="username" i]'
                )
                page.wait_for_selector(login_selector, timeout=25000)

                # Cloudflare Turnstile
                turnstile_input = page.locator('input[name="cf-turnstile-response"]')
                if turnstile_input.count() > 0:
                    if headless:
                        _close(context, browser)
                        return False, "Turnstile challenge. Run: python login_once.py leetcode"
                    logger.info("Turnstile detected. Complete it in the browser (2 min).")
                    try:
                        page.wait_for_function(
                            "() => { const el = document.querySelector('input[name=\"cf-turnstile-response\"]'); return el && el.value && el.value.length > 10; }",
                            timeout=120_000,
                        )
                    except Exception:
                        _close(context, browser)
                        return False, "Turnstile timeout. Run: python login_once.py leetcode"
                    time.sleep(2)

                submit_selector = 'button[type="submit"], button:has-text("Sign In"), button:has-text("Log In")'
                try:
                    page.wait_for_function(
                        "() => { const btn = document.querySelector('button[type=submit]') || Array.from(document.querySelectorAll('button')).find(b => /sign in|log in/i.test(b.textContent)); return btn && !btn.disabled; }",
                        timeout=15_000,
                    )
                except Exception:
                    pass

                page.locator(login_selector).first.fill(username)
                page.fill('input[name="password"], input[type="password"]', password)
                page.locator(submit_selector).first.click(timeout=15_000)
                page.wait_for_url("**/leetcode.com/**", timeout=25000)
                page.goto(contest_url, wait_until="domcontentloaded", timeout=35000)
                time.sleep(4)

            # --- On the contest page (logged in) ---
            # The page body contains "Users must register to participate" in the contest description,
            # so we CANNOT use broad text matching like "registered in body". We need precise checks.

            # Check if the user's avatar is visible (means logged in)
            avatar = page.locator('#navbar_user_avatar')
            if avatar.count() == 0:
                # Not logged in -- cookies may not have worked
                if use_stored_cookies:
                    _close(context, browser)
                    return False, "Cookies didn't log you in. Re-export from J2TEAM Cookies while logged in and re-import in Settings."

            # Look for the Register button specifically in the contest hero area.
            # LeetCode uses: <button ...><div ...><span>Register</span></div></button>
            # There may be multiple (hero + sticky header). We want the first visible one.
            # Use a JS approach to find the right button (not matching nav/other buttons).
            register_btn = page.locator('button:has(span:text-is("Register"))')

            if register_btn.count() == 0:
                # No Register button -- might already be registered (button changes to "Registered" or disappears)
                # Or contest hasn't opened registration yet
                # Check for "Registered" button or "Unregister" button (shown after registration)
                registered_indicator = page.locator('button:has(span:text-is("Registered")), button:has(span:text-is("Unregister")), button:has-text("Unregister")')
                if registered_indicator.count() > 0:
                    _close(context, browser)
                    return True, "Already registered for this contest"

                # Also check if the page shows a countdown or "Starts in" (contest exists but no Register button)
                starts_in = page.locator('text=/Starts in/')
                contest_ended = page.locator('text=/Contest has ended|Contest is over/i')
                if contest_ended.count() > 0:
                    _close(context, browser)
                    return False, "Contest has already ended"

                _close(context, browser)
                return False, "Could not find Register button. Contest may not be open for registration yet."

            # Click the Register button (opens the contest page registration)
            logger.info("Clicking Register button on LeetCode contest page")
            try:
                register_btn.first.click(timeout=10000)
            except Exception as click_err:
                # Try scrolling the button into view first
                logger.warning("First click failed (%s), trying scroll + click", click_err)
                register_btn.first.scroll_into_view_if_needed(timeout=5000)
                time.sleep(1)
                register_btn.first.click(timeout=10000)

            # LeetCode shows a confirmation modal: "Register Contest" with Cancel / Register.
            # We must click the "Register" button inside the modal to complete registration.
            time.sleep(1.5)
            dialog = page.get_by_role("dialog")
            if dialog.count() > 0:
                modal_register = dialog.get_by_role("button", name="Register")
                if modal_register.count() > 0:
                    logger.info("Clicking Register in confirmation modal")
                    modal_register.first.click(timeout=5000)
                    time.sleep(2)
            else:
                # Fallback: look for a modal container and Register inside it
                modal_register = page.locator('[role="dialog"] button:has(span:text-is("Register")), [role="dialog"] button:text-is("Register")')
                if modal_register.count() > 0:
                    logger.info("Clicking Register in confirmation modal (fallback)")
                    modal_register.first.click(timeout=5000)
                    time.sleep(2)

            # Wait for the page to react (SPA state update)
            time.sleep(2)

            # After clicking, check if registration succeeded:
            # 1. Register button disappears or changes to "Registered"/"Unregister"
            # 2. A success toast/notification appears
            post_body = page.content().lower()

            # Check if the Register button is now gone or changed
            register_btn_after = page.locator('button:has(span:text-is("Register"))')
            registered_btn = page.locator('button:has(span:text-is("Registered")), button:has(span:text-is("Unregister")), button:has-text("Unregister")')

            if registered_btn.count() > 0:
                _close(context, browser)
                return True, "Registered successfully"

            if register_btn_after.count() == 0:
                # Button disappeared -- likely registered
                _close(context, browser)
                return True, "Registered successfully (button changed)"

            # Button still there -- might have failed or needs confirmation
            # Check for any success message
            if "successfully" in post_body or "you are registered" in post_body:
                _close(context, browser)
                return True, "Registered successfully"

            # If still showing Register, try one more click
            logger.info("Register button still present, trying second click")
            try:
                register_btn_after.first.click(timeout=5000)
                time.sleep(3)
                final_reg = page.locator('button:has(span:text-is("Registered")), button:has(span:text-is("Unregister"))')
                if final_reg.count() > 0:
                    _close(context, browser)
                    return True, "Registered successfully"
            except Exception:
                pass

            _close(context, browser)
            return False, "Clicked Register but could not confirm success. Check on leetcode.com manually."
        except Exception as e:
            _close(context, browser)
            logger.exception("LeetCode registration error: %s", e)
            err = str(e)
            if "element is not enabled" in err or "not enabled" in err.lower():
                return False, "Button not ready. Run: python login_once.py leetcode"
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
