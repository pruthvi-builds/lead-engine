"""LLM-based name/title extraction — the paid-tier upgrade over the regex
heuristic in site_crawler.py. Feeds a page's visible text to Claude and asks
for structured {name, title} pairs via forced tool-use, which handles page
layouts the regex heuristic misses (unusual team-page structures, bios
written in prose, names embedded in paragraphs rather than card grids).

Fails soft everywhere: no API key, no network, a bad response, or any SDK
error all just return an empty list rather than breaking the crawl — this
is a quality *enhancement* layered on top of the always-available heuristic,
never a hard dependency.
"""
import os
import json

from .models import Person

MAX_PAGE_CHARS = 6000  # keep cost/latency predictable per page

EXTRACTION_TOOL = {
    "name": "extract_people",
    "description": "Extract every named individual and their job title mentioned on this "
                    "company webpage. Only include real, specific people (not generic roles, "
                    "not company/brand names, not nav menu items). If no title is stated, omit it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "people": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Full name of the person"},
                        "title": {"type": "string", "description": "Their job title/role, if stated on the page"},
                    },
                    "required": ["name"],
                },
            }
        },
        "required": ["people"],
    },
}


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and bool(os.environ.get("LLM_EXTRACTION_MODEL"))


def _get_client():
    import anthropic  # imported lazily so the dependency is only required when this path is used
    return anthropic.Anthropic()


def extract_people_llm(page_text: str, url: str) -> list:
    """Returns list[Person]. Never raises — any failure returns []."""
    if not llm_available():
        return []

    model = os.environ["LLM_EXTRACTION_MODEL"]
    text = page_text.strip()[:MAX_PAGE_CHARS]
    if not text:
        return []

    try:
        client = _get_client()
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "extract_people"},
            messages=[{
                "role": "user",
                "content": f"Webpage text (from {url}):\n\n{text}",
            }],
        )
    except Exception:
        return []

    people = []
    try:
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use" or block.name != "extract_people":
                continue
            for entry in block.input.get("people", []):
                name = (entry.get("name") or "").strip()
                if not name or len(name.split()) < 2:
                    continue  # require at least a first + last name
                title = (entry.get("title") or "").strip() or None
                people.append(Person(name=name, title=title, source_url=url))
    except Exception:
        return []

    return people


def parse_tool_input_for_test(raw_json: str) -> list:
    """Pure-logic helper used by tests to validate the parsing path without a live API call."""
    data = json.loads(raw_json)
    people = []
    for entry in data.get("people", []):
        name = (entry.get("name") or "").strip()
        if not name or len(name.split()) < 2:
            continue
        title = (entry.get("title") or "").strip() or None
        people.append(Person(name=name, title=title))
    return people
