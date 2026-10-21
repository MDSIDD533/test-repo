from datetime import datetime, timezone
from email.utils import format_datetime
from io import BytesIO

from app import news_provider


def test_news_provider_returns_only_matching_recent_sourced_items(monkeypatch):
    published = format_datetime(datetime.now(timezone.utc))
    xml = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>Bitcoin market update</title><link>https://example.com/btc</link>
      <description>Bitcoin details</description><pubDate>{published}</pubDate></item>
      <item><title>Ethereum market update</title><link>https://example.com/eth</link>
      <description>Ethereum details</description><pubDate>{published}</pubDate></item>
    </channel></rss>""".encode()
    class FakeResponse(BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
    monkeypatch.setattr(news_provider, "urlopen", lambda request, timeout: FakeResponse(xml))
    result = news_provider.fetch_crypto_news("BTCUSDT", "2026-01-01T00:00:00Z")
    assert len(result) == 1
    assert result[0]["title"] == "Bitcoin market update"
    assert result[0]["source"] == "CoinDesk RSS"
    assert result[0]["url"] == "https://example.com/btc"
    assert result[0]["published_at"]
