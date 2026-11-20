"""Monitor locale delle chiamate HTTP: nessuna richiesta aggiuntiva o credenziale salvata."""
import atexit
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

EVENTS = []
ORIGINAL_GET = requests.get
ORIGINAL_POST = requests.post
OUTPUT = Path("api_monitor.json")


def record(method, original):
    def wrapped(url, *args, **kwargs):
        start = time.monotonic()
        status = None
        remaining = None
        used = None
        error = None
        try:
            response = original(url, *args, **kwargs)
            status = response.status_code
            remaining = response.headers.get("x-requests-remaining")
            used = response.headers.get("x-requests-used")
            return response
        except requests.RequestException as exc:
            error = type(exc).__name__
            raise
        finally:
            host = urlsplit(str(url)).hostname or "unknown"
            entry = {
                "utc": datetime.now(timezone.utc).isoformat(),
                "service": ("telegram" if host == "api.telegram.org" else host),
                "method": method,
                "status": status,
                "duration_ms": round((time.monotonic() - start) * 1000),
            }
            if remaining is not None:
                entry["credits_remaining"] = remaining
            if used is not None:
                entry["credits_used"] = used
            if error:
                entry["error"] = error
            EVENTS.append(entry)
            print("API_MONITOR:", json.dumps(entry, ensure_ascii=False))
    return wrapped


def save():
    try:
        OUTPUT.write_text(json.dumps({
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "request_count": len(EVENTS),
            "requests": EVENTS,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print("API_MONITOR_SAVE_ERROR:", type(exc).__name__)


def install():
    requests.get = record("GET", ORIGINAL_GET)
    requests.post = record("POST", ORIGINAL_POST)
    atexit.register(save)
