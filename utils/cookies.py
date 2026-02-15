"""Parse cookies pasted by user (Netscape cookies.txt or JSON) into Playwright-ready format."""
import base64
import gzip
import json
from typing import Any

# Playwright add_cookies wants: name, value, domain, path; optional: expires, httpOnly, secure, sameSite


def _normalize_domain(domain: str, platform: str) -> bool:
    """Return True if domain is valid for the platform."""
    domain = (domain or "").strip().lower()
    if platform == "codeforces":
        return "codeforces.com" in domain
    if platform == "leetcode":
        return "leetcode.com" in domain
    return False


def _cookies_from_list(items: list, platform: str) -> list[dict[str, Any]]:
    """Build Playwright cookie list from a list of cookie-like dicts; filter by platform domain."""
    out: list[dict[str, Any]] = []
    default_domain = ".codeforces.com" if platform == "codeforces" else ".leetcode.com"
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("Name")
        value = item.get("value") or item.get("Value")
        domain = (item.get("domain") or item.get("Domain") or "").strip()
        if name is None and value is None:
            continue
        if not name:
            continue
        if not _normalize_domain(domain, platform):
            continue
        out.append({
            "name": str(name),
            "value": str(value) if value is not None else "",
            "domain": domain or default_domain,
            "path": str(item.get("path") or item.get("Path") or "/"),
        })
    return out


def parse_cookies_raw(paste: str, platform: str) -> list[dict[str, Any]]:
    """
    Parse pasted string as either Netscape cookies.txt or JSON array.
    Returns list of dicts with name, value, domain, path (and optional expires, httpOnly, secure).
    Only includes cookies whose domain matches the platform. Invalid/cross-domain cookies are dropped.
    """
    paste = (paste or "").strip()
    if not paste:
        return []

    out: list[dict[str, Any]] = []

    # Cookie-Editor export: {"url":"...", "cookies": [...]} — direct array of cookies
    if paste.startswith("{"):
        try:
            obj = json.loads(paste)
            if isinstance(obj, dict):
                arr = obj.get("cookies") or obj.get("cookie")
                if isinstance(arr, list):
                    out = _cookies_from_list(arr, platform)
                    if out:
                        return out
                # Backup format: {"url":"...", "version":2, "data":"<base64>"}
                if "data" in obj and isinstance(obj["data"], str):
                    raw = obj["data"]
                    pad = len(raw) % 4
                    if pad:
                        raw += "=" * (4 - pad)
                    try:
                        decoded = base64.b64decode(raw, validate=True)
                    except Exception:
                        try:
                            decoded = base64.urlsafe_b64decode(raw + "==")
                        except Exception:
                            decoded = b""
                    if len(decoded) >= 2 and decoded[:2] == b"\x1f\x8b":
                        try:
                            decoded = gzip.decompress(decoded)
                        except Exception:
                            pass
                    try:
                        inner = json.loads(decoded.decode("utf-8", errors="replace"))
                    except Exception:
                        inner = None
                    if inner is not None:
                        if isinstance(inner, list):
                            out = _cookies_from_list(inner, platform)
                        elif isinstance(inner, dict):
                            arr = inner.get("cookies") or inner.get("cookie") or inner.get("data")
                            if isinstance(arr, list):
                                out = _cookies_from_list(arr, platform)
                            elif isinstance(inner.get("domains"), dict):
                                for _domain, cookies in inner["domains"].items():
                                    if _normalize_domain(str(_domain), platform) and isinstance(cookies, list):
                                        out.extend(_cookies_from_list(cookies, platform))
                        if out:
                            return out
        except (json.JSONDecodeError, ValueError, gzip.BadGzipFile, KeyError):
            pass
        # Fall through: might be a single object that is one cookie (unlikely) or we continue to other formats

    # JSON array (e.g. [{"name":"x","value":"y","domain":".codeforces.com","path":"/"}] )
    if paste.startswith("["):
        try:
            data = json.loads(paste)
            if isinstance(data, list):
                out = _cookies_from_list(data, platform)
                if out:
                    return out
        except json.JSONDecodeError:
            pass

    # Netscape format: each line is tab-separated
    # domain  flag  path  secure  expiration  name  value
    # e.g. .codeforces.com	TRUE	/	TRUE	1739123456	JSESSIONID	abc123
    for line in paste.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, _secure, _exp, name, value = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
        if not _normalize_domain(domain, platform):
            continue
        out.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
        })
    return out


def parse_cookie_header(header_value: str, domain: str, platform: str) -> list[dict[str, Any]]:
    """
    Parse a Cookie header string (name1=value1; name2=value2) for a given domain.
    Only use if domain matches platform.
    """
    if not _normalize_domain(domain, platform):
        return []
    out = []
    for part in (header_value or "").split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if name:
            out.append({
                "name": name,
                "value": value,
                "domain": domain if domain.startswith(".") else f".{domain}",
                "path": "/",
            })
    return out
