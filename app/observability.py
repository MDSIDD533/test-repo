"""Structured event log with an operator-visible tail.

Two things were missing when something went wrong: a machine-readable record
and a way to see recent failures without reading a terminal. This module writes
JSON-lines events to disk and keeps a bounded in-memory ring buffer so the
dashboard can show recent problems.

Design rules:

- Never log headers, cookies or credentials. Known secret-bearing field names
  are redacted before anything is written.
- A logging failure must never break a request.
- The ring buffer is bounded; the file is append-only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RING_SIZE = 500
LEVELS = ("debug", "info", "warning", "error")
_REDACTED = "[redacted]"
_SENSITIVE_FRAGMENTS = (
    "token", "secret", "password", "passwd", "api_key", "apikey",
    "authorization", "cookie", "session", "private",
)
_RING: deque[dict[str, Any]] = deque(maxlen=RING_SIZE)
_LOCK = threading.Lock()


def log_path() -> Path:
    """Resolve the log path at call time so deployments and tests can override it."""
    configured = os.environ.get("APP_LOG_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "runtime" / "app.log"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if any(fragment in str(key).lower() for fragment in _SENSITIVE_FRAGMENTS):
                cleaned[key] = _REDACTED
            else:
                cleaned[key] = _redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    return value


def log_event(level: str, event: str, **fields: Any) -> dict[str, Any]:
    """Record one structured event. Never raises."""
    normalized = level.lower()
    if normalized not in LEVELS:
        normalized = "info"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": normalized,
        "event": str(event)[:200],
        **_redact(fields),
    }
    with _LOCK:
        _RING.append(entry)
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    except OSError:
        # Losing the file copy must not take down the request that logged it.
        pass
    return entry


def recent(limit: int = 100, *, level: str | None = None) -> list[dict[str, Any]]:
    """Return the newest events first, optionally filtered to one level or worse."""
    with _LOCK:
        entries = list(_RING)
    if level:
        floor = LEVELS.index(level.lower()) if level.lower() in LEVELS else 0
        entries = [item for item in entries if LEVELS.index(item["level"]) >= floor]
    return list(reversed(entries))[: max(0, limit)]


def summary() -> dict[str, Any]:
    """Counts by level for the whole retained buffer."""
    with _LOCK:
        entries = list(_RING)
    counts = {level: 0 for level in LEVELS}
    for entry in entries:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
    return {
        "retained": len(entries),
        "capacity": RING_SIZE,
        "counts": counts,
        "errors": counts.get("error", 0),
        "warnings": counts.get("warning", 0),
        "log_file": str(log_path()),
    }


def clear() -> None:
    """Empty the ring buffer. Used by tests; the on-disk file is not rewritten."""
    with _LOCK:
        _RING.clear()


def timed(level: str, event: str, started: float, **fields: Any) -> dict[str, Any]:
    """Log an event with a computed duration."""
    return log_event(level, event, duration_ms=round((time.perf_counter() - started) * 1000, 2), **fields)
