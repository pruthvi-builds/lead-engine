"""Generate candidate corporate email addresses for a person at a domain,
and infer a company's real pattern from any already-published email.
"""
import re
import unicodedata
from .models import Person, EmailCandidate

# (pattern_name, function(first, last) -> local_part), ordered by real-world frequency
PATTERNS = [
    ("{first}.{last}", lambda f, l: f"{f}.{l}"),
    ("{f}{last}", lambda f, l: f"{f[0]}{l}"),
    ("{first}", lambda f, l: f"{f}"),
    ("{first}{last}", lambda f, l: f"{f}{l}"),
    ("{last}.{first}", lambda f, l: f"{l}.{f}"),
    ("{first}_{last}", lambda f, l: f"{f}_{l}"),
    ("{f}.{last}", lambda f, l: f"{f[0]}.{l}"),
    ("{last}{f}", lambda f, l: f"{l}{f[0]}"),
    ("{first}{l}", lambda f, l: f"{f}{l[0]}"),
    ("{last}", lambda f, l: f"{l}"),
]

GENERIC_LOCAL_PARTS = {
    "info", "contact", "hello", "support", "sales", "admin", "office",
    "team", "help", "inquiries", "enquiries", "press", "media", "hr",
    "careers", "jobs", "billing", "accounts", "no-reply", "noreply",
}


def _clean(name_part: str) -> str:
    name_part = unicodedata.normalize("NFKD", name_part).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", name_part.lower())


def split_name(full_name: str):
    parts = full_name.strip().split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    return _clean(first), _clean(last)


def infer_pattern_from_known_email(known_email: str, known_person: Person):
    """If we have one real published email tied to a known named person, figure out
    which pattern the company uses so we can apply it with high confidence to everyone else."""
    if not known_person:
        return None
    split = split_name(known_person.name)
    if not split:
        return None
    first, last = split
    local = known_email.split("@")[0].lower()
    for name, fn in PATTERNS:
        try:
            if fn(first, last) == local:
                return name
        except IndexError:
            continue
    return None


def generate_candidates(person: Person, domain: str, known_pattern: str = None) -> list:
    """Return EmailCandidate list, best-guess first."""
    split = split_name(person.name)
    if not split:
        return []
    first, last = split
    if not first or not last:
        return []

    candidates = []
    pattern_list = PATTERNS
    if known_pattern:
        # put the confirmed company pattern first
        pattern_list = sorted(PATTERNS, key=lambda p: p[0] != known_pattern)

    for name, fn in pattern_list:
        try:
            local = fn(first, last)
        except IndexError:
            continue
        if not local or local in GENERIC_LOCAL_PARTS:
            continue
        email = f"{local}@{domain}"
        source = "inferred_pattern" if (known_pattern and name == known_pattern) else "generated"
        base_confidence = 0.85 if source == "inferred_pattern" else max(0.15, 0.5 - 0.05 * pattern_list.index((name, fn)))
        candidates.append(EmailCandidate(
            email=email, pattern=name, person=person, source=source, confidence=base_confidence
        ))
    return candidates
