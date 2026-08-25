"""Politely crawl a small set of public pages on a company's own site
(home, about, team, contact, leadership) and extract:
  - any email addresses the company has chosen to publish
  - named individuals + job titles (from team/leadership pages)

Respects robots.txt and uses a real, identifiable User-Agent + delay between
requests. Never touches any page requiring login, and never crawls beyond the
target's own domain.
"""
import re
import time
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import Person

USER_AGENT = "LeadEngineBot/0.1 (+contact: set-your-contact-email-here)"
REQUEST_DELAY_SECONDS = 1.5
TIMEOUT = 8

CANDIDATE_PATHS = [
    "", "/about", "/about-us", "/team", "/our-team", "/leadership",
    "/contact", "/contact-us", "/company", "/people",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")


def _clean_phone(raw):
    digits = re.sub(r"[^\d+]", "", raw)
    if len(re.sub(r"\D", "", digits)) < 7:
        return ""
    return digits
# Each word: capital letter then LOWERCASE only (rules out "THE PLATFORM"-style nav/caps text)
NAME_RE = re.compile(r"^[A-Z][a-z'\-]{1,20}(?:\s[A-Z][a-z'\-]{1,20}){1,2}$")

# Common non-name words that still pass the Title Case pattern (nav items, headings, etc.)
# — if either word in a candidate "name" is one of these, it's almost certainly not a person.
NAME_STOPWORDS = {
    "the", "by", "role", "for", "and", "with", "from", "home", "page", "menu",
    "platform", "communication", "product", "products", "solutions", "solution",
    "resources", "resource", "pricing", "customers", "customer", "company",
    "careers", "career", "blog", "news", "events", "event", "log", "sign",
    "get", "started", "learn", "more", "contact", "about", "read", "case",
    "studies", "study", "help", "center", "policy", "terms", "service",
    "privacy", "cookie", "cookies", "we", "our", "your", "you", "us",
}

TITLE_HINTS = (
    "ceo", "founder", "co-founder", "cofounder", "president", "director",
    "manager", "head of", "vp", "vice president", "cto", "coo", "cfo",
    "lead", "owner", "principal", "partner", "sales", "marketing", "growth",
)


# Common two-capitalized-word phrases that are NOT people's names — filters out the
# biggest source of false positives in the full-page fallback pass.
NAME_NOISE_PHRASES = {
    "united states", "new york", "san francisco", "privacy policy", "terms service",
    "log in", "sign up", "learn more", "get started", "contact us", "about us",
    "read more", "case study", "case studies", "customer stories", "help center",
}


def _extract_names_from_lines(text_block: str, url: str, require_title_hint: bool):
    """Scan a block of visible text line-by-line for "Name" followed (within 1 line) by
    something that reads like a job title. Returns list[Person]."""
    found = []
    lines = [l.strip() for l in text_block.split("\n") if l.strip()]
    for i, line in enumerate(lines):
        if not NAME_RE.match(line):
            continue
        if line.lower() in NAME_NOISE_PHRASES:
            continue
        if any(word.lower() in NAME_STOPWORDS for word in line.split()):
            continue
        title = ""
        window = lines[i + 1: i + 3]
        for cand in window:
            if any(h in cand.lower() for h in TITLE_HINTS):
                title = cand
                break
        if require_title_hint and not title:
            continue
        found.append(Person(name=line, title=title or None, source_url=url))
    return found


def _get_robot_parser(domain: str) -> robotparser.RobotFileParser:
    """Fetch robots.txt ourselves with a real User-Agent and hand the text to
    RobotFileParser. We do NOT use rp.read() directly — it uses urllib's default
    "Python-urllib/x.x" UA under the hood, which many CDNs (Cloudflare etc.) 403,
    and RobotFileParser silently treats a 403 as "disallow everything". That false
    negative would make us skip perfectly public, crawlable sites.
    """
    rp = robotparser.RobotFileParser()
    rp.set_url(f"https://{domain}/robots.txt")
    try:
        resp = requests.get(f"https://{domain}/robots.txt",
                             headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            # No robots.txt (404) or blocked (403/etc) — assume allowed, since a
            # missing robots.txt conventionally means "no restrictions declared".
            rp.parse(["User-agent: *", "Allow: /"])
    except requests.RequestException:
        rp.parse(["User-agent: *", "Allow: /"])
    return rp


def _allowed(rp: robotparser.RobotFileParser, url: str) -> bool:
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def crawl_company_pages(domain: str, max_pages: int = 6, use_llm: bool = False) -> dict:
    """Returns {"emails": dict[str, str] (email -> first page URL found on),
    "people": list[Person], "pages_crawled": list[str]}.

    use_llm=True additionally runs each crawled page through the LLM extractor
    (lead_engine.llm_extractor) — catches names/titles the regex heuristic
    misses (prose bios, unusual layouts). No-ops silently if no API key is
    configured; intended to be gated to paid users by the caller since it
    costs a model call per page.
    """
    base = f"https://{domain}"
    rp = _get_robot_parser(domain)

    emails_found = {}  # email -> first page URL it was found on, for accurate lead sourcing
    phones_found = set()
    people = []
    pages_crawled = []

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for path in CANDIDATE_PATHS[:max_pages]:
        url = urljoin(base, path)
        if not _allowed(rp, url):
            continue
        try:
            resp = session.get(url, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        finally:
            time.sleep(REQUEST_DELAY_SECONDS)

        if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
            continue

        pages_crawled.append(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. mailto: links are the highest-confidence, explicitly-published emails
        for a in soup.find_all("a", href=True):
            if a["href"].lower().startswith("mailto:"):
                addr = a["href"].split(":", 1)[1].split("?")[0].strip()
                if EMAIL_RE.fullmatch(addr):
                    emails_found.setdefault(addr.lower(), url)
            if a["href"].lower().startswith("tel:"):
                raw = a["href"].split(":", 1)[1].split("?")[0].strip()
                cleaned = _clean_phone(raw)
                if cleaned:
                    phones_found.add(cleaned)

        # 2. any plaintext emails on the page
        for m in EMAIL_RE.findall(soup.get_text(" ")):
            emails_found.setdefault(m.lower(), url)

        for m in PHONE_RE.findall(soup.get_text(" ")):
            cleaned = _clean_phone(m)
            if cleaned:
                phones_found.add(cleaned)

        # 3a. heuristic name+title extraction, scoped to team/leadership-looking sections
        #     (highest precision — the surrounding markup itself signals "this is a person")
        sections = soup.find_all(
            lambda tag: tag.get("class") and any(
                kw in " ".join(tag.get("class")).lower()
                for kw in ("team", "staff", "people", "member", "leadership", "founder")
            )
        )
        for section in sections:
            people.extend(_extract_names_from_lines(section.get_text("\n"), url, require_title_hint=False))

        # 3b. full-page fallback pass — many marketing sites (esp. static/Webflow pages)
        # don't tag their "About/Founders" blurb with a team-ish class at all. Here we scan
        # the whole page but require a title-hint word within 1 line, to keep precision high
        # (a bare two-capitalized-word phrase alone is too noisy — could be anything).
        if not sections:
            people.extend(_extract_names_from_lines(soup.get_text("\n"), url, require_title_hint=True))

        # 3c. LLM pass (paid-tier only, opt-in) — catches prose bios and layouts neither
        # heuristic above matches. Runs per crawled page; fails soft to [] if unavailable.
        if use_llm:
            from .llm_extractor import extract_people_llm
            people.extend(extract_people_llm(soup.get_text(" "), url))

    # de-duplicate people by name, keep first occurrence
    seen = set()
    deduped_people = []
    for p in people:
        if p.name not in seen:
            seen.add(p.name)
            deduped_people.append(p)

    return {"emails": emails_found, "phones": phones_found, "people": deduped_people, "pages_crawled": pages_crawled}
