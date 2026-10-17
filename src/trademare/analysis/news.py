"""Recent headlines for a company from Google News RSS (free, no key; good India coverage)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests

from trademare.core.models import Market

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_REGION = {Market.IN: ("en-IN", "IN", "IN:en"), Market.US: ("en-US", "US", "US:en")}
_SUFFIXES = re.compile(
    r"\b(ltd\.?|limited|inc\.?|corp\.?|corporation|plc|co\.?|company|the)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Headline:
    title: str
    source: str
    published: datetime


def search_name(company: str) -> str:
    """'Infosys Ltd.' -> 'Infosys'; keeps the distinctive part of the name."""
    name = _SUFFIXES.sub("", company).replace("&amp;", "&")
    return re.sub(r"\s+", " ", name).strip(" .,")


def parse_rss(content: bytes, max_items: int) -> list[Headline]:
    root = ET.fromstring(content)
    out = []
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        source = item.findtext("source") or ""
        # Google appends " - <source>" to titles; drop it to save tokens.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        out.append(Headline(title=title.strip(), source=source, published=published.astimezone(UTC)))
    out.sort(key=lambda h: h.published, reverse=True)
    return out[:max_items]


def fetch_headlines(market: Market, company: str, lookback_days: int, max_items: int) -> list[Headline]:
    hl, gl, ceid = _REGION[market]
    params = {"q": f'"{search_name(company)}" when:{lookback_days}d', "hl": hl, "gl": gl, "ceid": ceid}
    resp = requests.get(
        GOOGLE_NEWS_RSS, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0 TradeMare"}
    )
    resp.raise_for_status()
    return parse_rss(resp.content, max_items)
