"""CLI entrypoint: python -m lead_engine.cli "Company Name" [--domain acme.com] [--csv out.csv]"""
import argparse
import csv
import json
import sys

from .engine import generate_leads


def leads_to_rows(result: dict):
    rows = []
    for lead in result["leads"]:
        best = lead.emails[0] if lead.emails else None
        rows.append({
            "company": lead.company,
            "domain": lead.domain,
            "name": lead.person.name if lead.person else "",
            "title": (lead.person.title or "") if lead.person else "",
            "email": best.email if best else "",
            "email_confidence": round(best.confidence, 2) if best else 0,
            "email_source": best.source if best else "",
            "mx_valid": best.mx_valid if best else None,
            "catch_all_domain": best.catch_all_domain if best else None,
            "smtp_deliverable": best.smtp_deliverable if best else None,
            "alt_emails": "; ".join(e.email for e in lead.emails[1:]) if lead.emails else "",
            "source_urls": "; ".join(lead.source_urls),
            "notes": "; ".join(best.notes) if best else "",
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description="Generate leads from public company data.")
    parser.add_argument("company_name", nargs="?", default="", help="Company name to search for")
    parser.add_argument("--domain", default="", help="Skip search, use this domain directly")
    parser.add_argument("--location", default="", help="Optional location hint, e.g. 'Mumbai'")
    parser.add_argument("--no-smtp", action="store_true", help="Skip SMTP verification (faster, more polite)")
    parser.add_argument("--use-llm", action="store_true",
                         help="Also run LLM-based name/title extraction (needs ANTHROPIC_API_KEY + "
                              "LLM_EXTRACTION_MODEL env vars set)")
    parser.add_argument("--csv", default="", help="Write results to this CSV path")
    parser.add_argument("--json", default="", help="Write raw results to this JSON path")
    args = parser.parse_args()

    if not args.company_name and not args.domain:
        parser.error("Provide a company_name or --domain")

    result = generate_leads(
        company_name=args.company_name,
        domain=args.domain,
        location_hint=args.location,
        attempt_smtp=not args.no_smtp,
        use_llm=args.use_llm,
    )

    for w in result["warnings"]:
        print(f"[warning] {w}", file=sys.stderr)

    rows = leads_to_rows(result)
    print(f"\nResolved domain: {result['domain']}  (via {result['resolved_via']})")
    print(f"Pages crawled: {len(result['pages_crawled'])}")
    print(f"Leads found: {len(rows)}\n")

    for r in rows:
        print(f"- {r['name'] or '(unnamed contact)':<28} {r['title'] or '':<25} "
              f"{r['email']:<35} conf={r['email_confidence']}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else
                                     ["company", "domain", "name", "title", "email"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {args.csv}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=2, default=str)
        print(f"Wrote raw JSON to {args.json}")


if __name__ == "__main__":
    main()
