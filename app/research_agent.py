"""Research Agent with explicit provenance and freshness checks.

A provider is injected by the application; this module never invents headlines
or treats missing external research as evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse


ResearchProvider = Callable[[str, str], list[dict[str, Any]]]


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Research item must include published_at as an ISO timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("Research published_at must be a valid ISO timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("Research published_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def make_research_agent(
    provider: ResearchProvider,
    *,
    clock: Callable[[], datetime] | None = None,
    max_age: timedelta = timedelta(hours=72),
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build a research stage around a provider with verifiable source metadata."""
    if not callable(provider):
        raise ValueError("A research provider is required")
    if max_age.total_seconds() <= 0:
        raise ValueError("max_age must be positive")

    def run(context: dict[str, Any]) -> dict[str, Any]:
        symbol = str(context.get("symbol", "")).strip().upper()
        as_of = str(context.get("as_of", "")).strip()
        if not symbol:
            raise ValueError("A non-empty symbol is required")
        if not as_of:
            raise ValueError("Pipeline as_of timestamp is required")
        try:
            reference_time = clock() if clock else datetime.now(timezone.utc)
            if reference_time.tzinfo is None:
                raise ValueError("Research clock must return a timezone-aware datetime")
            reference_time = reference_time.astimezone(timezone.utc)
            raw_items = provider(symbol, as_of)
        except Exception as exc:
            raise RuntimeError(f"Research provider failed: {type(exc).__name__}: {exc}") from exc
        if not isinstance(raw_items, list):
            raise ValueError("Research provider must return a list")
        if not raw_items:
            raise ValueError("No research evidence available; refusing to fabricate a summary")

        evidence: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise ValueError(f"Research item {index} must be an object")
            title = str(item.get("title", "")).strip()
            source = str(item.get("source", "")).strip()
            url = str(item.get("url", "")).strip()
            if not title or not source or not url:
                raise ValueError(f"Research item {index} requires title, source, and url")
            parsed_url = urlparse(url)
            if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
                raise ValueError(f"Research item {index} has an invalid source URL")
            published_at = _parse_timestamp(item.get("published_at"))
            age = reference_time - published_at
            if age < timedelta(minutes=-5):
                raise ValueError(f"Research item {index} is dated in the future")
            if age > max_age:
                continue
            evidence.append({
                "title": title,
                "source": source,
                "url": url,
                "published_at": published_at.isoformat(),
                "age_hours": round(max(0.0, age.total_seconds()) / 3600, 2),
                "summary": str(item.get("summary", "")).strip(),
            })
        if not evidence:
            raise ValueError("No fresh research evidence available for this run")
        evidence.sort(key=lambda item: item["published_at"], reverse=True)
        return {
            "symbol": symbol,
            "as_of": as_of,
            "status": "evidence_available",
            "items_reviewed": len(raw_items),
            "items_accepted": len(evidence),
            "evidence": evidence,
            "warnings": ["Research evidence provides context; it does not prove a future price move."],
        }

    return run
