"""Small public RSS provider; no API key required. Network failures are surfaced safely."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.request import Request, urlopen
from xml.etree import ElementTree


RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"


def fetch_crypto_news(symbol: str, as_of: str) -> list[dict[str, str]]:
    """Fetch recent CoinDesk RSS items and keep symbol-relevant headlines.

    This provider deliberately returns source metadata and never fabricates items.
    The pipeline's research agent performs the freshness and URL validation.
    """
    normalized = symbol.strip().upper()
    base = re.sub(r"(USDT|USDC|BUSD|USD|BTC|ETH)$", "", normalized) if normalized.endswith(("USDT", "USDC", "BUSD", "USD")) else normalized
    aliases = {
        "BTC": ("bitcoin", "btc"),
        "ETH": ("ethereum", "ether", "eth"),
        "BNB": ("binance coin", "bnb"),
        "SOL": ("solana", "sol"),
        "XRP": ("xrp", "ripple"),
        "DOGE": ("dogecoin", "doge"),
        "ADA": ("cardano", "ada"),
        "AVAX": ("avalanche", "avax"),
        "LINK": ("chainlink", "link"),
        "SUI": ("sui",),
    }
    tokens = aliases.get(base, (base.lower(),))
    request = Request(RSS_URL, headers={"User-Agent": "SiddTradingPro/1.0 (market research RSS reader)"})
    with urlopen(request, timeout=5) as response:
        payload = response.read(2_000_000)
    root = ElementTree.fromstring(payload)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("News RSS feed has no channel")
    now = datetime.now(timezone.utc)
    items: list[dict[str, str]] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not title or not link or not pub_date:
            continue
        try:
            published = parsedate_to_datetime(pub_date)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            published = published.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
        if (now - published).total_seconds() > 72 * 3600:
            continue
        text = f"{title} {description}".lower()
        if not any(token in text for token in tokens):
            continue
        items.append({
            "title": title,
            "source": "CoinDesk RSS",
            "url": link,
            "published_at": published.isoformat(),
            "summary": re.sub(r"<[^>]+>", " ", description).strip()[:1000],
        })
        if len(items) >= 10:
            break
    return items
