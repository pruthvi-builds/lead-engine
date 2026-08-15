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
    # review/listing/directory aggregators that show up in category searches
    # but are never themselves the company being searched for
    "goodfirms.co", "clutch.co", "g2.com", "capterra.com", "trustpilot.com",
    "sitejabber.com", "softwaresuggest.com", "thetoptens.com", "mouthshut.com",
    "justdial.com", "sulekha.com", "indiamart.com", "tradeindia.com",
    "99acres.com", "magicbricks.com", "housing.com", "squareyards.com",
    "urbanpro.com", "quora.com", "yellowpages.com", "siliconindia.com",
    "trustindex.io", "expertise.com", "thumbtack.com", "angi.com",
    "ambitionbox.com", "glassdoor.co.in", "naukri.com", "shine.com",
    "timesjobs.com", "monster.com", "foundit.in",
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


def find_companies_by_category(category: str, location: str = "", max_results: int = 8) -> list:
    """Search for multiple companies in a given category/niche (+ optional location).

    Returns a de-duplicated list of candidate root domains (best-effort, free web
    search via DDGS), filtered against BLOCKLIST_DOMAINS. Order is roughly by
    relevance/frequency in the search results.
    """
    if DDGS is None:
        raise RuntimeError("duckduckgo_search is not installed")

    category = (category or "").strip()
    if not category:
        return []

    query = f"{category} {location} companies".strip() if location else f"{category} companies"
    candidates = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 4):
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                domain = _root_domain(url)
                if not domain or domain in BLOCKLIST_DOMAINS:
                    continue
                candidates.append(domain)
    except Exception:
        return []

    seen_order = []
    counts = {}
    for d in candidates:
        counts[d] = counts.get(d, 0) + 1
        if d not in seen_order:
            seen_order.append(d)
    seen_order.sort(key=lambda d: (-counts[d], candidates.index(d)))
    return seen_order[:max_results]
