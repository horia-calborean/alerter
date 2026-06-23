#!/usr/bin/env python3
"""Watch one or more web pages and push an ntfy alert when their content changes.

State is a plain-text snapshot per watch under state/<slug>.txt. The snapshot IS
the state: each run compares the freshly extracted text against the stored one,
and git history of those files doubles as a human-readable change log.

Config: config.json   Secret: NTFY_TOPIC (env). Set DRY_RUN=1 to print instead of send.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Ensure UTF-8 stdout so printed diffs with diacritics don't crash a cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
CONFIG_PATH = ROOT / "config.json"

# A browser-like UA: the bare python-requests UA is blocked by some sites.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 page-monitor/1.0"
)
HTTP_TIMEOUT = 30
DIFF_MAX_LINES = 40   # lines of diff included in the alert body
DIFF_MAX_CHARS = 3500  # hard cap on alert body length


def gh_warning(msg: str) -> None:
    """Print a GitHub Actions warning annotation (shows in the run summary)."""
    print(f"::warning::{msg}")


def slugify(text: str) -> str:
    """ASCII-safe filesystem slug (diacritics stripped for the filename only)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "watch"


def fetch_text(url: str, selector: str | None) -> str:
    """Fetch the page and return normalized visible text of the target region.

    Decoding is forced through BeautifulSoup's byte sniffing (reads <meta charset>)
    so UTF-8 pages with diacritics (Romanian, Norwegian, ...) decode correctly
    instead of being mangled by requests' latin-1 fallback.
    """
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()

    # Pass raw bytes so bs4 detects the real encoding from the HTML meta tags.
    soup = BeautifulSoup(resp.content, "html.parser")

    # Drop non-content / volatile nodes.
    for tag in soup(["script", "style", "noscript", "svg", "template", "iframe"]):
        tag.decompose()

    region = soup.select_one(selector) if selector else None
    if region is None:
        region = soup.body or soup  # fall back to whole document

    text = region.get_text("\n", strip=True)
    # Collapse blank runs and trailing spaces so cosmetic reflow is not a "change".
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def make_diff(old: str, new: str) -> str:
    diff_lines = list(
        unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile="before", tofile="after", lineterm="",
        )
    )
    # Keep the header plus the most relevant changed lines.
    body = "\n".join(diff_lines[:DIFF_MAX_LINES])
    if len(diff_lines) > DIFF_MAX_LINES:
        body += f"\n... (+{len(diff_lines) - DIFF_MAX_LINES} more diff lines)"
    if len(body) > DIFF_MAX_CHARS:
        body = body[:DIFF_MAX_CHARS] + "\n... (truncated)"
    return body


def notify(server: str, topic: str, title: str, message: str,
           click: str, tags: list[str], priority: int) -> None:
    """Publish to ntfy via its JSON API (full UTF-8 support for title + body)."""
    if os.environ.get("DRY_RUN") == "1" or not topic:
        print("--- NOTIFICATION (dry-run) ---")
        print("title:", title)
        print("click:", click)
        print(message)
        print("--- end ---")
        return
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "click": click,
        "tags": tags,
        "priority": priority,
    }
    r = requests.post(server, json=payload, timeout=HTTP_TIMEOUT)
    r.raise_for_status()


def process_watch(watch: dict, server: str, topic: str) -> bool:
    """Return True on a clean run (incl. 'no change'), False on fetch failure."""
    name = watch["name"]
    url = watch["url"]
    selector = watch.get("selector")
    slug = slugify(name)
    snap_path = STATE_DIR / f"{slug}.txt"

    try:
        new_text = fetch_text(url, selector)
    except Exception as exc:  # network / HTTP / parse error -> warn, keep old state
        gh_warning(f"[{name}] fetch failed: {type(exc).__name__}: {exc}")
        return False

    if not new_text.strip():
        gh_warning(f"[{name}] extracted empty content (selector '{selector}'?) - skipping")
        return False

    new_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()[:12]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not snap_path.exists():
        snap_path.write_text(new_text, encoding="utf-8")
        print(f"[{name}] baseline saved ({len(new_text)} chars, {new_hash})")
        notify(
            server, topic,
            title=f"Monitoring started: {name}",
            message=f"Baseline captured at {now}. You will be alerted on any change.",
            click=url, tags=["eyes"], priority=2,
        )
        return True

    old_text = snap_path.read_text(encoding="utf-8")
    if old_text == new_text:
        print(f"[{name}] no change ({new_hash})")
        return True

    print(f"[{name}] CHANGE DETECTED ({new_hash})")
    diff = make_diff(old_text, new_text)
    snap_path.write_text(new_text, encoding="utf-8")
    notify(
        server, topic,
        title=f"Page changed: {name}",
        message=f"Detected at {now}\n\n{diff}",
        click=url, tags=["rotating_light"], priority=4,
    )
    return True


def main() -> int:
    STATE_DIR.mkdir(exist_ok=True)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server = config.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    topic = os.environ.get("NTFY_TOPIC", "").strip()

    if not topic and os.environ.get("DRY_RUN") != "1":
        gh_warning("NTFY_TOPIC is not set - running in print-only mode (no push sent)")

    watches = config.get("watches", [])
    if not watches:
        print("No watches configured in config.json", file=sys.stderr)
        return 1

    all_ok = True
    for watch in watches:
        all_ok = process_watch(watch, server, topic) and all_ok

    # Fetch hiccups are surfaced as warnings, not hard failures, so the cron
    # stays green for transient site outages. Return 0 regardless of fetch errors.
    return 0


if __name__ == "__main__":
    sys.exit(main())
