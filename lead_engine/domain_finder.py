"""Resolve a company name to its most likely official domain, using public search results.

This intentionally does NOT scrape any social network or gated platform. It queries a
public search engine for the company's official site, which is the same thing a human
researcher would do by hand.
"""
import re
from urllib.parse import urlparse

import tldextract

try:
    from ddgs import DDGS  # current package name (duckduckgo_search was renamed)
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # fallback for older installs
    except ImportError:
        DDGS = None

# Domains that are almost never a company's own site (aggregators, socials, directories)
BLOCKLIST_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "wikipedia.org", "crunchbase.com", "glassdoor.com",
    "indeed.com", "yelp.com", "bloomberg.com", "github.com", "medium.com",
    "reddit.com", "pinterest.com", "tiktok.com", "amazon.com",
}


def _root_domain(url: str) -> str:
    ext = tldextract.extract(url)
    if not ext.domain:
        return ""
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def find_domain(company_name: str, hint_location: str = "") -> str:
    """Return the best-guess root domain (e.g. 'acme.com') for a company name.

    Returns "" if nothing confident was found — callers should treat that as
    'needs manual input' rather than guessing further.
    """
    if DDGS is None:
        raise RuntimeError("duckduckgo_search is not installed")

    query = f"{company_name} {hint_location} official website".strip()
    candidates = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=8):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                domain = _root_domain(url)
                if not domain or domain in BLOCKLIST_DOMAINS:
                    continue
                candidates.append(domain)
    except Exception:
        return ""

    if not candidates:
        return ""

    # Prefer the domain that appears earliest/most often among results
    seen_order = []
    counts = {}
    for d in candidates:
        counts[d] = counts.get(d, 0) + 1
        if d not in seen_order:
            seen_order.append(d)
    seen_order.sort(key=lambda d: (-counts[d], candidates.index(d)))
    return seen_order[0]


def normalize_domain(raw: str) -> str:
    """Accepts a domain, bare host, or full URL and returns the clean root domain."""
    raw = raw.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    return _root_domain(raw)
