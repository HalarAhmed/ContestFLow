"""One-time login: opens a browser for Codeforces and LeetCode so you can log in manually.
Your session (cookies) is saved to .playwright-sessions/ and reused by auto-registration.

Usage:
    python login_once.py              # log in to both
    python login_once.py codeforces   # log in to Codeforces only
    python login_once.py leetcode     # log in to LeetCode only
"""
import sys
import time

from playwright.sync_api import sync_playwright
from automation.browser_session import get_session_dir
from automation.stealth import launch_persistent_context, STEALTH_SCRIPT


def login_interactive(platform: str) -> None:
    url = {
        "codeforces": "https://codeforces.com/enter",
        "leetcode": "https://leetcode.com/accounts/login/",
    }[platform]

    session_dir = get_session_dir(platform)
    print(f"\n{'='*60}")
    print(f"  Opening {platform.title()} login page...")
    print(f"  Session will be saved to: {session_dir}")
    print(f"{'='*60}")
    print(f"  1. Log in to your {platform.title()} account in the browser window.")
    print(f"  2. Complete any CAPTCHA / 'Verify you are human' checks.")
    print(f"  3. Once you see your profile / dashboard, CLOSE the browser window.")
    print(f"{'='*60}\n")

    with sync_playwright() as p:
        context = launch_persistent_context(p, session_dir, headless=False, viewport={"width": 1280, "height": 800})
        context.add_init_script(STEALTH_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for user to log in and close the browser
        print("Waiting for you to log in and close the browser...")
        try:
            while True:
                try:
                    # This will throw when the browser is closed
                    page.title()
                    time.sleep(1)
                except Exception:
                    break
        except KeyboardInterrupt:
            pass

        try:
            context.close()
        except Exception:
            pass

    print(f"\n  Session saved for {platform.title()}!")
    print(f"  Auto-registration will now skip the login step.\n")


def main():
    platforms = sys.argv[1:] if len(sys.argv) > 1 else ["codeforces", "leetcode"]
    for p in platforms:
        p = p.lower().strip()
        if p not in ("codeforces", "leetcode"):
            print(f"Unknown platform: {p}. Use 'codeforces' or 'leetcode'.")
            continue
        login_interactive(p)
    print("All done! You can now use 'Register (auto)' from the dashboard.")


if __name__ == "__main__":
    main()
