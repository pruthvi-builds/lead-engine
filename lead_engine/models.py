"""Core data models for the lead-generation engine."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Person:
    """A named individual found on a public company page."""
    name: str
    title: Optional[str] = None
    source_url: str = ""


@dataclass
class EmailCandidate:
    """A generated or discovered email address with a confidence score."""
    email: str
    pattern: str            # which pattern produced it, e.g. "{first}.{last}"
    person: Optional[Person] = None
    source: str = "generated"   # "generated" | "found_on_page" | "inferred_pattern"
    mx_valid: Optional[bool] = None
    smtp_deliverable: Optional[bool] = None   # None = unknown/skipped, True/False = probed
    catch_all_domain: Optional[bool] = None
    confidence: float = 0.0     # 0-1
    notes: list = field(default_factory=list)


@dataclass
class Lead:
    """A fully assembled lead: a person + their best email candidate(s) at a company."""
    company: str
    domain: str
    person: Optional[Person]
    emails: list  # list[EmailCandidate], best first
    source_urls: list = field(default_factory=list)
    phones: list = field(default_factory=list)  # cleaned phone number strings
