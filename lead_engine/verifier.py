"""Verify email candidates without spamming anyone or breaking any ToS.

Three layers, cheapest/most-reliable first:
  1. Syntax check (free, instant)
  2. MX record lookup — does the domain even accept mail? (free, DNS only)
  3. SMTP RCPT TO probe — ask the mail server if the mailbox exists, without
     sending anything (free, but many providers block outbound port 25, and
     many mail servers are "catch-all" and will lie and accept everything —
     both cases are detected and reported honestly rather than guessed at)

If port 25 is blocked (common on cloud hosts) or the server doesn't cooperate,
candidates fall back to pattern-confidence + MX-validity only, and that's
surfaced clearly rather than faked as "verified".
"""
import random
import re
import smtplib
import socket
import string

import dns.resolver

from .models import EmailCandidate

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
SMTP_TIMEOUT = 4


def _random_local_part(n=16):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def get_mx_records(domain: str):
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return sorted(
            [(r.preference, str(r.exchange).rstrip(".")) for r in answers],
            key=lambda x: x[0],
        )
    except Exception:
        return []


def _smtp_probe(mx_host: str, from_addr: str, rcpt_addr: str):
    """Returns True/False/None (None = couldn't determine, e.g. port blocked or greylisted)."""
    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as smtp:
            smtp.helo("leadengine.local")
            smtp.mail(from_addr)
            code, _ = smtp.rcpt(rcpt_addr)
            return code in (250, 251)
    except (socket.timeout, socket.error, smtplib.SMTPException, OSError):
        return None


def check_catch_all(domain: str, mx_host: str) -> bool:
    """If a made-up address is accepted, the domain accepts all mail —
    SMTP verification for any specific address on it is meaningless."""
    fake_addr = f"{_random_local_part()}@{domain}"
    result = _smtp_probe(mx_host, f"verify@{domain}", fake_addr)
    return bool(result)  # True = catch-all, None/False both mean "not confirmed catch-all"


def verify_candidate(candidate: EmailCandidate, mx_cache: dict = None, attempt_smtp: bool = True) -> EmailCandidate:
    mx_cache = mx_cache if mx_cache is not None else {}
    domain = candidate.email.split("@")[1]

    if not EMAIL_RE.match(candidate.email):
        candidate.mx_valid = False
        candidate.confidence = 0.0
        candidate.notes.append("failed syntax check")
        return candidate

    if domain not in mx_cache:
        mx_cache[domain] = get_mx_records(domain)
    mx_records = mx_cache[domain]

    if not mx_records:
        candidate.mx_valid = False
        candidate.confidence *= 0.1
        candidate.notes.append("no MX records — domain cannot receive mail")
        return candidate

    candidate.mx_valid = True
    top_mx = mx_records[0][1]

    if not attempt_smtp:
        candidate.notes.append("SMTP probe skipped")
        return candidate

    catch_all_key = f"__catchall__{domain}"
    if catch_all_key not in mx_cache:
        mx_cache[catch_all_key] = check_catch_all(domain, top_mx)
    is_catch_all = mx_cache[catch_all_key]
    candidate.catch_all_domain = is_catch_all

    if is_catch_all:
        candidate.notes.append("domain is catch-all — SMTP cannot confirm this specific mailbox")
        candidate.confidence = min(candidate.confidence, 0.55)
        return candidate

    result = _smtp_probe(top_mx, f"verify@{domain}", candidate.email)
    candidate.smtp_deliverable = result
    if result is True:
        candidate.confidence = max(candidate.confidence, 0.9)
        candidate.notes.append("SMTP confirmed mailbox exists")
    elif result is False:
        candidate.confidence *= 0.05
        candidate.notes.append("SMTP rejected — mailbox likely does not exist")
    else:
        candidate.notes.append("SMTP probe inconclusive (port 25 blocked or server did not respond) — "
                                "confidence based on pattern + MX only")
    return candidate
