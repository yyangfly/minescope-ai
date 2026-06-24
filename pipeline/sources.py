from __future__ import annotations

import json
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from pipeline.models import RawRecord
from pipeline.text import parse_datetime

USER_AGENT = "mining-intel-interview/1.0"


def fetch_json(url: str, timeout: int = 25) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 25) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"})
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def rss_text(element: ET.Element, name: str) -> str:
    child = element.find(name)
    return child.text.strip() if child is not None and child.text else ""


def parse_rss_datetime(value: str) -> datetime:
    try:
        dt = parsedate_to_datetime(value)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return parse_datetime(value)


class RSSNewsSource:
    name = "rss_news"
    category = "news"

    GOOGLE_QUERIES = [
        "mining OR mines OR miners when:30d",
        "lithium mining OR lithium export OR lithium price when:30d",
        "copper mining OR copper mine OR copper price when:30d",
        "nickel mining OR nickel export OR nickel policy when:30d",
        "\"critical minerals\" mining when:30d",
        "\"iron ore\" mining price export when:30d",
        "\"rare earth\" mining supply policy when:30d",
    ]

    MEDIA_FEEDS = [
        "https://www.mining.com/feed/",
        "https://im-mining.com/feed/",
        "https://www.australianmining.com.au/feed/",
        "https://www.mining-technology.com/feed/",
        "https://www.northernminer.com/feed/",
    ]

    def __init__(self, limit: int = 300) -> None:
        self.limit = limit

    def collect(self) -> list[RawRecord]:
        records: list[RawRecord] = []
        for query in self.GOOGLE_QUERIES:
            url = (
                "https://news.google.com/rss/search?q="
                + quote_plus(query)
                + "&hl=en-US&gl=US&ceid=US:en"
            )
            records.extend(self._collect_feed(url, source_name="google_news_rss", query=query))
            time.sleep(0.4)
            if len(records) >= self.limit:
                return records[: self.limit]

        for url in self.MEDIA_FEEDS:
            try:
                records.extend(self._collect_feed(url, source_name="mining_media_rss", query=url))
            except Exception as exc:
                print(f"RSS feed failed: {url}: {exc}")
            if len(records) >= self.limit:
                break
        return records[: self.limit]

    def _collect_feed(self, url: str, source_name: str, query: str) -> list[RawRecord]:
        xml_text = fetch_text(url)
        root = ET.fromstring(xml_text)
        rows: list[RawRecord] = []
        for item in root.findall(".//item"):
            title = rss_text(item, "title")
            link = rss_text(item, "link")
            description = rss_text(item, "description")
            pub_date = parse_rss_datetime(rss_text(item, "pubDate"))
            if pub_date < datetime.now(timezone.utc) - timedelta(days=35):
                continue
            source_node = item.find("source")
            publisher = source_node.text.strip() if source_node is not None and source_node.text else ""
            rows.append(
                RawRecord(
                    source=source_name,
                    category=self.category,
                    external_id=link or title,
                    title=title,
                    body=description or title,
                    url=link,
                    published_at=pub_date,
                    metadata={"query": query, "publisher": publisher},
                )
            )
        return rows


class RSSPolicySource:
    name = "rss_policy"
    category = "policy"

    GOOGLE_QUERIES = [
        "\"critical minerals\" policy government when:30d",
        "mining regulation government when:30d",
        "lithium export policy government when:30d",
        "copper mining policy government when:30d",
        "nickel export policy government when:30d",
        "\"rare earth\" policy government when:30d",
        "site:gov.au critical minerals mining policy when:30d",
        "site:canada.ca critical minerals mining policy when:30d",
        "site:energy.gov critical minerals mining policy when:30d",
        "site:europa.eu critical raw materials mining policy when:30d",
    ]

    def __init__(self, limit: int = 250) -> None:
        self.limit = limit

    def collect(self) -> list[RawRecord]:
        records: list[RawRecord] = []
        for query in self.GOOGLE_QUERIES:
            url = (
                "https://news.google.com/rss/search?q="
                + quote_plus(query)
                + "&hl=en-US&gl=US&ceid=US:en"
            )
            try:
                records.extend(self._collect_feed(url, query))
            except Exception as exc:
                print(f"Policy RSS query failed: {query}: {exc}")
            time.sleep(0.4)
            if len(records) >= self.limit:
                break
        return records[: self.limit]

    def _collect_feed(self, url: str, query: str) -> list[RawRecord]:
        xml_text = fetch_text(url)
        root = ET.fromstring(xml_text)
        rows: list[RawRecord] = []
        for item in root.findall(".//item"):
            title = rss_text(item, "title")
            link = rss_text(item, "link")
            description = rss_text(item, "description")
            pub_date = parse_rss_datetime(rss_text(item, "pubDate"))
            if pub_date < datetime.now(timezone.utc) - timedelta(days=35):
                continue
            source_node = item.find("source")
            publisher = source_node.text.strip() if source_node is not None and source_node.text else ""
            rows.append(
                RawRecord(
                    source="google_news_policy_rss",
                    category=self.category,
                    external_id=link or title,
                    title=title,
                    body=description or title,
                    url=link,
                    published_at=pub_date,
                    metadata={"query": query, "publisher": publisher},
                )
            )
        return rows


class GDELTNewsSource:
    name = "gdelt_news"
    category = "news"

    def __init__(self, limit: int = 250) -> None:
        self.limit = min(limit, 250)

    def collect(self) -> list[RawRecord]:
        query = '("mining" OR "critical minerals" OR lithium OR copper OR nickel OR "iron ore")'
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(self.limit),
            "timespan": "1month",
            "sort": "datedesc",
            "format": "json",
        }
        data = fetch_json("https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params))
        rows = []
        for item in data.get("articles", []):
            rows.append(
                RawRecord(
                    source=self.name,
                    category=self.category,
                    external_id=item.get("url", ""),
                    title=item.get("title", ""),
                    body=" ".join(
                        part
                        for part in [
                            item.get("title", ""),
                            item.get("domain", ""),
                            item.get("sourcecountry", ""),
                            item.get("language", ""),
                        ]
                        if part
                    ),
                    url=item.get("url", ""),
                    published_at=parse_datetime(item.get("seendate")),
                    metadata={
                        "domain": item.get("domain"),
                        "source_country": item.get("sourcecountry"),
                        "language": item.get("language"),
                    },
                )
            )
        return rows


class PolicySource:
    name = "policy_feeds"
    category = "policy"

    def __init__(self, limit: int = 250) -> None:
        self.limit = limit

    def collect(self) -> list[RawRecord]:
        records: list[RawRecord] = []
        for collector in (self._collect_gdelt_policy, self._collect_federal_register):
            try:
                records.extend(collector())
            except Exception as exc:
                print(f"Policy sub-source failed: {collector.__name__}: {exc}")
        return records[: self.limit]

    def _collect_gdelt_policy(self) -> list[RawRecord]:
        query = (
            '("critical minerals" OR mining OR lithium OR copper OR nickel OR "rare earth") '
            "(domain:gov.au OR domain:canada.ca OR domain:energy.gov OR domain:usgs.gov "
            "OR domain:europa.eu OR domain:gov.uk OR domain:industry.gov.au)"
        )
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(min(self.limit, 250)),
            "timespan": "1month",
            "sort": "datedesc",
            "format": "json",
        }
        data = fetch_json("https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params))
        rows = []
        for item in data.get("articles", []):
            rows.append(
                RawRecord(
                    source="gdelt_policy_domains",
                    category=self.category,
                    external_id=item.get("url", ""),
                    title=item.get("title", ""),
                    body=" ".join(
                        part
                        for part in [item.get("title", ""), item.get("domain", ""), item.get("sourcecountry", "")]
                        if part
                    ),
                    url=item.get("url", ""),
                    published_at=parse_datetime(item.get("seendate")),
                    metadata={"domain": item.get("domain"), "source_country": item.get("sourcecountry")},
                )
            )
        return rows

    def _collect_federal_register(self) -> list[RawRecord]:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        records: list[RawRecord] = []
        for term in ["mining", "critical minerals", "lithium", "copper", "rare earth"]:
            params = [
                ("conditions[term]", term),
                ("conditions[publication_date][gte]", since),
                ("order", "newest"),
                ("per_page", "100"),
                ("fields[]", "title"),
                ("fields[]", "abstract"),
                ("fields[]", "html_url"),
                ("fields[]", "publication_date"),
                ("fields[]", "document_number"),
                ("fields[]", "agencies"),
            ]
            data = fetch_json("https://www.federalregister.gov/api/v1/articles.json?" + urlencode(params))
            for item in data.get("results", []):
                agencies = [agency.get("name") for agency in item.get("agencies", []) if agency.get("name")]
                records.append(
                    RawRecord(
                        source="federal_register",
                        category=self.category,
                        external_id=item.get("document_number", item.get("html_url", "")),
                        title=item.get("title", ""),
                        body=item.get("abstract", "") or " ".join(agencies),
                        url=item.get("html_url", ""),
                        published_at=parse_datetime(item.get("publication_date")),
                        metadata={"agencies": agencies, "term": term},
                    )
                )
        return records


class PriceSource:
    name = "yahoo_chart"
    category = "price"

    DEFAULT_SYMBOLS = [
        "BHP",
        "RIO",
        "VALE",
        "FCX",
        "SCCO",
        "ALB",
        "SQM",
        "LIT",
        "COPX",
        "PICK",
        "GLNCY",
        "NEM",
    ]

    def __init__(self, symbols: Iterable[str] | None = None) -> None:
        self.symbols = list(symbols or self.DEFAULT_SYMBOLS)

    def collect(self) -> list[RawRecord]:
        records: list[RawRecord] = []
        for symbol in self.symbols:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=45d&interval=1d"
                data = fetch_json(url)
            except Exception as exc:
                print(f"Price symbol failed: {symbol}: {exc}")
                continue
            result = (data.get("chart", {}).get("result") or [{}])[0]
            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            meta = result.get("meta") or {}
            for idx, ts in enumerate(timestamps):
                close = _safe_index(quote.get("close"), idx)
                if close is None:
                    continue
                dt = parse_datetime(ts)
                open_ = _safe_index(quote.get("open"), idx)
                high = _safe_index(quote.get("high"), idx)
                low = _safe_index(quote.get("low"), idx)
                volume = _safe_index(quote.get("volume"), idx)
                body = (
                    f"{symbol} mining-market price on {dt.date()}: "
                    f"open={open_}, high={high}, low={low}, close={close}, volume={volume}."
                )
                records.append(
                    RawRecord(
                        source=self.name,
                        category=self.category,
                        external_id=f"{symbol}:{dt.date().isoformat()}",
                        title=f"{symbol} close {close:.2f} on {dt.date().isoformat()}",
                        body=body,
                        url=f"https://finance.yahoo.com/quote/{symbol}",
                        published_at=dt,
                        metadata={
                            "symbol": symbol,
                            "currency": meta.get("currency"),
                            "exchange": meta.get("exchangeName"),
                            "open": open_,
                            "high": high,
                            "low": low,
                            "close": close,
                            "volume": volume,
                        },
                    )
                )
        return records


def _safe_index(values: list | None, idx: int):
    if not values or idx >= len(values):
        return None
    return values[idx]


def fixture_records(per_category: int = 200) -> list[RawRecord]:
    rng = random.Random(42)
    now = datetime.now(timezone.utc)
    minerals = ["lithium", "copper", "nickel", "iron ore", "rare earth"]
    countries = ["Australia", "Canada", "Chile", "Indonesia", "United States"]
    records: list[RawRecord] = []

    for idx in range(per_category):
        mineral = minerals[idx % len(minerals)]
        country = countries[idx % len(countries)]
        dt = now - timedelta(days=idx % 30, hours=idx % 24)
        records.append(
            RawRecord(
                source="fixture_news",
                category="news",
                external_id=f"fixture-news-{idx}",
                title=f"{country} {mineral} miners update production outlook #{idx}",
                body=f"Market participants reported changes in {mineral} supply, mine approvals, and export demand.",
                url=f"https://example.com/news/{idx}",
                published_at=dt,
                metadata={"fixture": True, "country": country, "mineral": mineral},
            )
        )
        records.append(
            RawRecord(
                source="fixture_policy",
                category="policy",
                external_id=f"fixture-policy-{idx}",
                title=f"{country} critical minerals policy notice on {mineral} exports #{idx}",
                body=f"The notice discusses permitting, export controls, royalties, and consultation for {mineral}.",
                url=f"https://example.com/policy/{idx}",
                published_at=dt,
                metadata={"fixture": True, "country": country, "mineral": mineral},
            )
        )
        price = 50 + rng.random() * 20 + idx * 0.03
        records.append(
            RawRecord(
                source="fixture_price",
                category="price",
                external_id=f"FIX{idx % 12}:{dt.date()}:{idx}",
                title=f"FIX{idx % 12} close {price:.2f} on {dt.date().isoformat()}",
                body=f"FIX{idx % 12} mining price close={price:.2f}; commodity context includes {mineral}.",
                url=f"https://example.com/price/{idx % 12}",
                published_at=dt,
                metadata={"fixture": True, "symbol": f"FIX{idx % 12}", "close": round(price, 2), "mineral": mineral},
            )
        )
    return records


def default_sources(limit_per_source: int = 250):
    return [RSSNewsSource(limit_per_source), GDELTNewsSource(limit_per_source), RSSPolicySource(limit_per_source), PolicySource(limit_per_source), PriceSource()]
