"""Top-level orchestration: company name/domain -> list[Lead]."""
from .domain_finder import find_domain, normalize_domain
from .site_crawler import crawl_company_pages
from .email_patterns import generate_candidates, infer_pattern_from_known_email, split_name
from .verifier import verify_candidate
from .models import Lead, Person, EmailCandidate


def generate_leads(company_name: str = "", domain: str = "", location_hint: str = "",
                    attempt_smtp: bool = True, max_people: int = 25, use_llm: bool = False) -> dict:
    """
    use_llm: run the LLM extraction pass in addition to the regex heuristic
    (lead_engine.llm_extractor) — higher accuracy, costs a model call per
    crawled page, needs ANTHROPIC_API_KEY + LLM_EXTRACTION_MODEL set. No-ops
    to heuristic-only if unavailable. Intended to be gated to paid users.

    Returns:
      {
        "domain": str,
        "resolved_via": "provided" | "search",
        "pages_crawled": [...],
        "leads": [Lead, ...],
        "warnings": [str, ...],
      }
    """
    warnings = []

    if domain:
        resolved_domain = normalize_domain(domain)
        resolved_via = "provided"
    elif company_name:
        resolved_domain = find_domain(company_name, location_hint)
        resolved_via = "search"
        if not resolved_domain:
            return {"domain": "", "resolved_via": "search", "pages_crawled": [],
                    "leads": [], "warnings": ["Could not confidently resolve a domain for "
                                               f"'{company_name}'. Provide the domain directly."]}
    else:
        raise ValueError("Provide either company_name or domain")

    if use_llm:
        from .llm_extractor import llm_available
        if not llm_available():
            warnings.append("use_llm was requested but ANTHROPIC_API_KEY/LLM_EXTRACTION_MODEL "
                             "isn't configured — falling back to heuristic-only extraction.")

    crawl = crawl_company_pages(resolved_domain, use_llm=use_llm)
    published_emails = crawl["emails"]
    people = crawl["people"][:max_people]

    # Try to infer the company's real email pattern from one person we can tie
    # to a published email (e.g. same name appears near a mailto: link).
    known_pattern = None
    for email in published_emails:
        local = email.split("@")[0]
        for p in people:
            split = split_name(p.name)
            if split and (split[0] in local or split[1] in local):
                known_pattern = infer_pattern_from_known_email(email, p)
                if known_pattern:
                    break
        if known_pattern:
            break

    if not people and not published_emails:
        warnings.append(
            f"No named individuals or published emails found on {resolved_domain}'s "
            "public pages. The site may not have a team/about page, or blocks robots.txt "
            "crawling. Consider adding domain-level generic contacts only, or supplying "
            "names manually."
        )

    mx_cache = {}
    leads = []

    for person in people:
        candidates = generate_candidates(person, resolved_domain, known_pattern)
        # Cheap pass first (syntax + MX only, no network round-trip beyond DNS) on every
        # candidate, then spend the slow SMTP probe only on the single best-ranked one —
        # SMTP has multi-second timeouts, so probing every pattern for every person doesn't
        # scale past a handful of names.
        cheap_pass = [verify_candidate(c, mx_cache, attempt_smtp=False) for c in candidates]
        cheap_pass.sort(key=lambda c: c.confidence, reverse=True)
        if attempt_smtp and cheap_pass:
            cheap_pass[0] = verify_candidate(cheap_pass[0], mx_cache, attempt_smtp=True)
        verified = cheap_pass
        verified.sort(key=lambda c: c.confidence, reverse=True)
        leads.append(Lead(
            company=company_name or resolved_domain,
            domain=resolved_domain,
            person=person,
            emails=verified[:3],  # top 3 candidates, not just one guess
            source_urls=[person.source_url] if person.source_url else [],
            phones=list(crawl.get("phones", [])),
        ))

    # Any directly-published emails with no name attached still count as a (lower-detail) lead
    unattributed = published_emails - {
        e.email for lead in leads for e in lead.emails if e.source == "found_on_page"
    }
    for email in unattributed:
        local = email.split("@")[0]
        if local in {"info", "contact", "hello", "support", "admin", "sales", "office"}:
            continue  # generic mailbox, not a person-level lead
        candidate = EmailCandidate(
            email=email, pattern="published", source="found_on_page", confidence=0.9,
            notes=["directly published on company site"],
        )
        candidate = verify_candidate(candidate, mx_cache, attempt_smtp=attempt_smtp)
        leads.append(Lead(
            company=company_name or resolved_domain,
            domain=resolved_domain,
            person=None,
            emails=[candidate],
            source_urls=crawl["pages_crawled"],
            phones=list(crawl.get("phones", [])),
        ))

    return {
        "domain": resolved_domain,
        "resolved_via": resolved_via,
        "pages_crawled": crawl["pages_crawled"],
        "phones": sorted(crawl.get("phones", [])),
        "leads": leads,
        "warnings": warnings,
    }
