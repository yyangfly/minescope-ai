from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}

ZH_EN_SYNONYMS = {
    "近7天": "last 7 days recent week",
    "近 7 天": "last 7 days recent week",
    "澳洲": "australia australian au",
    "澳大利亚": "australia australian au",
    "锂": "lithium li",
    "铜": "copper cu",
    "镍": "nickel ni",
    "铁矿": "iron ore",
    "出口": "export exports shipment trade",
    "进口": "import imports trade",
    "政策": "policy regulation law government ministry",
    "价格": "price close market stock commodity",
    "收盘": "close closing price stock",
    "收盘价": "close closing price stock",
    "股价": "stock price close market",
    "矿业": "mining minerals mine",
}


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    )
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def content_hash(*parts: str) -> str:
    normalized = "\n".join(normalize_text(part).lower() for part in parts if part)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_datetime(value: str | int | float | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    raw = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d%H%M%S",
        "%Y%m%dT%H%M%SZ",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=dt.tzinfo or timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def expand_query(query: str) -> str:
    expanded = [query]
    compact = re.sub(r"\s+", "", query)
    for key, value in ZH_EN_SYNONYMS.items():
        if key in query or key in compact:
            expanded.append(value)
    return " ".join(expanded)


def tokenize(text: str) -> list[str]:
    expanded = expand_query(normalize_text(text).lower())
    return re.findall(r"[\w\-]+", expanded, flags=re.UNICODE)
