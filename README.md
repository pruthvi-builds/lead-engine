**Live demo:** not yet deployed — runs locally (see below)
**Tech stack:** Python, FastAPI, SQLAlchemy, bcrypt auth, Dodo Payments (billing + signed webhooks), Claude API (LLM extraction), Postgres/SQLite

Full-stack lead-generation SaaS: give it a company name, get back named contacts with verified/scored email addresses, built entirely on public data. Includes auth, a DB-backed freemium quota, subscription billing with cryptographically-verified webhook events, and an optional LLM extraction pass.

# Lead Engine — full-stack public-data lead generation SaaS

Give it a company name or domain, get back named contacts with verified/scored
email addresses. Built entirely on public data — no LinkedIn scraping, no
purchased lists — so there's no ToS or data-privacy landmine sitting under it.
Runs at **$0 infrastructure cost** to start (SQLite + free-tier search, no
paid data broker), with the free→paid seam already wired in, not just planned.

This is a complete, tested app: core engine (+ optional LLM extraction pass)
+ auth + DB-backed freemium quota + Dodo Payments billing + a working web
frontend. Confirmed end-to-end in a real browser: signup → search → real
leads → CSV export. The full payment lifecycle was tested with real
cryptographically-signed webhook events (not mocked) — see "Billing" below.

## Quick start

```bash
pip install -r requirements.txt
uvicorn webapp.main:app --reload
# open http://localhost:8000
```

That's the whole product running locally — sign up, search a company, see
leads, export CSV. No external services required for the free tier.

## What it actually found, tested live against real companies

```
python3 -m lead_engine.cli --domain close.com --no-smtp
```
```
Resolved domain: close.com  (via provided)
Pages crawled: 6
Leads found: 12

- Steli Efti          CEO, Co-Founder              steli.efti@close.com   conf=0.5
- Anthony Nemitz      COO, Co-Founder              anthony.nemitz@close.com conf=0.5
- Phil Freo           VP of Product & Engineering  phil.freo@close.com    conf=0.5
...
```

Real published emails are picked up directly and verified (not guessed):
```
python3 -m lead_engine.cli --domain basecamp.com
# -> jason@basecamp.com, found_on_page, MX valid, not catch-all, conf=0.9
```

## Architecture

```
lead_engine/          the core engine (works standalone via CLI too)
  domain_finder.py     company name -> domain, via public search results
  site_crawler.py      politely crawls the company's own public pages (robots.txt-aware)
  email_patterns.py    generates + ranks candidate emails, infers the company's real pattern
  verifier.py          syntax -> MX lookup -> SMTP probe, honest about what it can't confirm
  llm_extractor.py     optional LLM-based name/title extraction (paid-tier upgrade)
  engine.py            orchestrates all of the above
  cli.py               command-line interface

webapp/                the full SaaS: auth, billing, freemium gating, API
  db.py                SQLAlchemy models (User, ApiKey, UsageEvent) — SQLite by default,
                        set DATABASE_URL to point at Postgres when you outgrow it
  auth.py               bcrypt password hashing, API-key issuance, quota enforcement
  billing.py            Dodo Payments Checkout + webhook handling (graceful no-op until configured)
  main.py                FastAPI app wiring it all together + serving the frontend

frontend/index.html    single-page UI: signup/login, search, results table, CSV export,
                        bulk mode, upgrade button — talks to the API directly, no build step
```

## How the engine works (pipeline)

1. **Domain resolution** — company name → domain via public search (the same
   thing a human researcher does by hand). Skipped if you already have the domain.
2. **Crawl** — fetches home/about/team/leadership/contact pages, respecting
   `robots.txt`, with a real identifiable User-Agent and a delay between requests.
   Extracts any email the company has already published (`mailto:` links +
   plaintext — zero guessing, highest confidence) and named people + titles via
   a three-pass approach:
   - Heuristic pass 1: scoped to team/leadership-looking page sections.
   - Heuristic pass 2: full-page fallback that only accepts a name if a
     job-title keyword sits right next to it (this is what keeps nav-menu
     text and other noise out — confirmed empirically: close.com's team page
     returned exactly 12 real leaders, zero false positives).
   - **LLM pass (optional, paid tier)** — see below.
3. **Pattern generation** — ~10 common corporate email patterns per person. If
   one real published email is tied to a known person, the company's actual
   pattern is inferred from it and applied to everyone else at much higher
   confidence.
4. **Verification** — syntax → MX lookup → SMTP `RCPT TO` probe. Catch-all
   domains are detected and flagged rather than faked as "verified." SMTP is
   only probed on the single best-ranked candidate per person to keep
   interactive lookups fast; the rest fall back to pattern + MX confidence.

Every lead carries its **source URL**.

## LLM-based extraction (paid-tier upgrade, implemented)

`lead_engine/llm_extractor.py` feeds each crawled page's text to Claude via
forced tool-use (a JSON schema requiring `{name, title}` pairs), which catches
layouts the regex heuristic misses — prose bios, unusual page structures.
It's wired into the pipeline as an additive pass (`use_llm=True` on
`generate_leads`/`crawl_company_pages`) and fails soft to heuristic-only
everywhere: no API key configured, a network error, a malformed response —
all just return `[]` from the LLM pass rather than breaking the crawl.

Verified: the response-parsing logic is unit-tested against a fixed sample
tool-call response (`llm_extractor.parse_tool_input_for_test`) — confirmed it
correctly extracts valid `{name, title}` pairs and filters out single-word
non-names and empty entries. The live API call path itself needs your own
`ANTHROPIC_API_KEY` to exercise (not available in the environment this was
built in) — the code path, error handling, and parsing are real and tested;
only the live network round-trip is unverified until you set the key.

**In the web app**, paid users get this automatically — `webapp/main.py`
passes `use_llm=user.is_paid` on every lookup, no separate toggle needed.

Env vars:
```
ANTHROPIC_API_KEY=sk-ant-...
LLM_EXTRACTION_MODEL=...      # set to a current model ID from docs.claude.com/en/docs/about-claude/models
                               # (deliberately not hardcoded here to avoid shipping a stale/wrong ID)
```
CLI flag: `python3 -m lead_engine.cli --domain acme.com --use-llm`

## Billing — Dodo Payments (implemented, real-signature tested)

Uses the official `dodopayments` Python SDK. Two things worth knowing about
this SDK if you extend it: the checkout resource is `client.checkout_sessions`
(not `client.checkouts`), and webhook verification is `client.webhooks.unwrap(...)`
following the [Standard Webhooks](https://standardwebhooks.com/) spec (three
headers: `webhook-id`, `webhook-signature`, `webhook-timestamp`) — both
confirmed by inspecting the installed SDK directly rather than assumed.

**What was actually tested** (not just written): a full, genuinely signed
webhook round-trip — generated a real HMAC signature with the `standardwebhooks`
library the same way Dodo's servers do, POSTed a `subscription.active` event
to the running `/billing/webhook` endpoint, and confirmed:
- A valid signature is accepted, the matching user is looked up by the
  checkout email, and `is_paid` flips to `True` with `dodo_customer_id`/
  `dodo_subscription_id` stored.
- A tampered signature is rejected with `400`.
- A `subscription.cancelled` event correctly flips the same user back to
  `is_paid = False`.
- `subscription.on_hold` (a recoverable payment-retry state per Dodo's docs)
  deliberately does **not** revoke access — only `cancelled`/`expired`/`failed` do.

Env vars (from your Dodo Payments dashboard — API Keys and Webhooks sections
at https://app.dodopayments.com):
```
DODO_PAYMENTS_API_KEY=...
DODO_PAYMENTS_ENVIRONMENT=test_mode        # or live_mode
DODO_PAYMENTS_WEBHOOK_KEY=whsec_...        # from the webhook you create in the dashboard
DODO_PRODUCT_ID=pdt_...                    # your subscription product's ID
PUBLIC_BASE_URL=https://yourdomain.com
```
Point a Dodo webhook at `POST /billing/webhook` for at least `subscription.active`,
`subscription.renewed`, `subscription.cancelled`, `subscription.expired`,
`subscription.failed` — all are already handled. Without these env vars set,
`/billing/create-checkout-session` returns a clear `501` instead of crashing —
the free tier works fully with zero billing config.

## Freemium (live-tested)

- 5 free lookups/day per account, enforced by a DB-backed daily usage ledger
  — verified against the running API: 5 calls succeed, the 6th returns `402`.
- Bulk lookup (`/leads/bulk`) and the LLM extraction pass are both gated to
  paid accounts — verified: flipping `user.is_paid = True` (exactly what the
  Dodo webhook does on a real payment, tested above) immediately unlocks bulk
  and removes the daily cap.

## What's legitimately excluded, on purpose

- No LinkedIn/social platform scraping (their ToS ban is the main legal
  exposure point for most "lead gen" tools).
- No purchased/leaked data lists.
- No sending — this tool finds and scores contacts, it doesn't email anyone.
  Cold outreach at scale needs its own CAN-SPAM/GDPR consent + unsubscribe
  layer; that's a deliberately separate feature, not bolted on here.
- Generic mailboxes (info@, support@, etc.) are filtered from named-lead
  results — not useful sales contacts, just clutter.

## Known limitations (honest, current state)

- The LLM extraction pass's live network path is untested (no API key was
  available in the build environment) — the parsing/error-handling logic is
  unit-tested and the integration is wired correctly, but verify it against
  a real key before relying on it.
- SMTP verification needs outbound port 25 open and a cooperative mail server;
  where that's blocked, confidence falls back to pattern + MX only.
- Public search (`ddgs`) is unofficial/unauthenticated and can rate-limit
  under heavy load.
- The SQLite usage ledger is a simple query, not yet optimized for scale —
  fine for hundreds of users, revisit past that.
- Bulk mode runs sequentially in-request with a 50-company cap — for larger
  batches, move to a background job queue (Celery/RQ) rather than raising the cap.
- Dodo customer matching on webhook events falls back to email lookup (no
  `customer_id` is returned at checkout-session creation time by the API) —
  correct as long as checkout email matches the account email, which it
  always will here since we pass `user.email` as the checkout customer.

## Next steps

1. **Verify the LLM extraction path against a real Anthropic key.**
2. **Postgres** — swap `DATABASE_URL` when you outgrow SQLite; zero code changes needed.
3. **Background jobs for bulk** — so large batches don't block a request thread.
4. **CRM export** — push straight to HubSpot/Close/Google Sheets.
5. **Go live** — real Dodo Payments keys + a production `PUBLIC_BASE_URL`.

Happy to build any of these next.
