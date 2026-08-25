"""Regression tests for lead source-URL accuracy.

Every lead is supposed to carry the specific page it was found on — that's
the whole point of showing a "source" link ("the exact source page, so you
can judge quality before you send anything", per the README). Two bugs
here would silently break that promise:

1. `crawl_company_pages` used to track found emails in a plain `set`, which
   discards which page each one came from.
2. `generate_leads`'s unattributed-email path (a published email with no
   name attached) used to source every such lead from the *entire* list of
   crawled pages rather than the one page the email actually appeared on —
   so the "source" link was really just whichever page got crawled first.
"""
from unittest.mock import patch

from lead_engine import engine
from lead_engine.models import EmailCandidate


def _passthrough_verify(candidate, mx_cache=None, attempt_smtp=True):
    """Stub for verify_candidate — skips real DNS/SMTP, keeps the candidate as-is."""
    candidate.mx_valid = True
    return candidate


def test_unattributed_email_sources_from_its_own_page_not_every_crawled_page():
    fake_crawl = {
        "emails": {"press@acme.com": "https://acme.com/press"},
        "phones": [],
        "people": [],
        "pages_crawled": [
            "https://acme.com",
            "https://acme.com/about",
            "https://acme.com/press",
        ],
    }

    with patch.object(engine, "crawl_company_pages", return_value=fake_crawl), \
         patch.object(engine, "verify_candidate", side_effect=_passthrough_verify):
        result = engine.generate_leads(domain="acme.com", attempt_smtp=False)

    assert len(result["leads"]) == 1
    lead = result["leads"][0]
    assert lead.emails[0].email == "press@acme.com"
    # The bug: this used to equal all three crawled pages instead of just the one.
    assert lead.source_urls == ["https://acme.com/press"]


def test_multiple_unattributed_emails_each_get_their_own_source_page():
    fake_crawl = {
        "emails": {
            "sales@acme.com": "https://acme.com/contact",
            "press@acme.com": "https://acme.com/press",
        },
        "phones": [],
        "people": [],
        "pages_crawled": ["https://acme.com", "https://acme.com/contact", "https://acme.com/press"],
    }

    with patch.object(engine, "crawl_company_pages", return_value=fake_crawl), \
         patch.object(engine, "verify_candidate", side_effect=_passthrough_verify):
        result = engine.generate_leads(domain="acme.com", attempt_smtp=False)

    by_email = {lead.emails[0].email: lead.source_urls for lead in result["leads"]}
    assert by_email["sales@acme.com"] == ["https://acme.com/contact"]
    assert by_email["press@acme.com"] == ["https://acme.com/press"]
