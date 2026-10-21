from datetime import datetime, timedelta, timezone

import pytest

from app.research_agent import make_research_agent


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def item(**overrides):
    value = {
        "title": "BTC spot market update",
        "source": "Example Research",
        "url": "https://example.com/research/btc",
        "published_at": (NOW - timedelta(hours=2)).isoformat(),
        "summary": "A source-attributed market note.",
    }
    value.update(overrides)
    return value


def context():
    return {"symbol": "btcusdt", "as_of": NOW.isoformat()}


def test_research_agent_accepts_recent_source_attributed_evidence():
    seen = []
    agent = make_research_agent(
        lambda symbol, as_of: seen.append((symbol, as_of)) or [item()],
        clock=lambda: NOW,
    )
    result = agent(context())
    assert seen == [("BTCUSDT", NOW.isoformat())]
    assert result["status"] == "evidence_available"
    assert result["items_accepted"] == 1
    assert result["evidence"][0]["source"] == "Example Research"
    assert result["evidence"][0]["url"].startswith("https://")


def test_research_agent_discards_stale_items():
    stale = item(published_at=(NOW - timedelta(days=5)).isoformat())
    agent = make_research_agent(lambda *_: [stale], clock=lambda: NOW)
    with pytest.raises(ValueError, match="No fresh research"):
        agent(context())


def test_research_agent_rejects_missing_provenance():
    invalid = item()
    invalid.pop("url")
    agent = make_research_agent(lambda *_: [invalid], clock=lambda: NOW)
    with pytest.raises(ValueError, match="title, source, and url"):
        agent(context())


def test_research_agent_rejects_future_dated_evidence():
    future = item(published_at=(NOW + timedelta(hours=1)).isoformat())
    agent = make_research_agent(lambda *_: [future], clock=lambda: NOW)
    with pytest.raises(ValueError, match="future"):
        agent(context())


def test_research_agent_does_not_fabricate_when_provider_is_empty():
    agent = make_research_agent(lambda *_: [], clock=lambda: NOW)
    with pytest.raises(ValueError, match="No research evidence"):
        agent(context())


def test_research_agent_requires_timezone_aware_publication_time():
    invalid = item(published_at="2026-10-21T11:00:00")
    agent = make_research_agent(lambda *_: [invalid], clock=lambda: NOW)
    with pytest.raises(ValueError, match="include a timezone"):
        agent(context())
