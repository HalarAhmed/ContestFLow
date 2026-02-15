"""Reduce automation detection so Cloudflare/Turnstile are less likely to show 'verification failed'.

- Use real Chrome when installed (channel='chrome').
- Launch with --disable-blink-features=AutomationControlled so navigator.webdriver is not set.
- Optionally inject a small script to mask remaining automation hints.
"""

# Chromium args that reduce "automation" fingerprint (verification failed on Cloudflare/Turnstile).
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-infobars",
    "--window-position=0,0",
    "--ignore-certificate-errors",
]

# Script injected into every page to mask navigator.webdriver and related props.
STEALTH_SCRIPT = """
(function() {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: function() { return undefined; }, configurable: true });
  } catch (e) {}
  try {
    if (window.chrome && window.chrome.runtime) {} else { window.chrome = { runtime: {} }; }
  } catch (e) {}
})();
"""


def launch_options():
    """Kwargs for launch_persistent_context / new_context to reduce verification failures."""
    return {
        "args": LAUNCH_ARGS,
        "ignore_default_args": ["--enable-automation"],
    }


def launch_persistent_context(p, user_data_dir: str, headless: bool = False, **extra):
    """Launch persistent context with anti-detection. Tries Chrome then Chromium."""
    kwargs = {
        "user_data_dir": user_data_dir,
        "headless": headless,
        "viewport": {"width": 1280, "height": 720},
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "locale": "en-US",
        **launch_options(),
        **extra,
    }
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        return p.chromium.launch_persistent_context(**kwargs)
