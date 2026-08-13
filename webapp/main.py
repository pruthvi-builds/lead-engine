"""Full app: signup/login, API-key auth, DB-backed freemium quota, Dodo
Payments billing, the core lead-gen engine (with optional LLM extraction for
paid users), and a bulk endpoint — all in one FastAPI service. Serves the
static frontend too, so this single process is the whole product for
local/early deployment.

Run:
    uvicorn webapp.main:app --reload
Then open http://localhost:8000
"""
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from .db import init_db, get_db, User
from . import auth, billing
from lead_engine.engine import generate_leads

app = FastAPI(title="Lead Engine", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    init_db()


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    return auth.require_user(authorization, db)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@app.post("/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user, api_key = auth.create_user(db, req.email, req.password)
    return {"email": user.email, "api_key": api_key}


@app.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = auth.authenticate(db, req.email, req.password)
    api_key = auth.get_active_api_key(db, user)
    return {"email": user.email, "api_key": api_key}


@app.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "email": user.email,
        "is_paid": user.is_paid,
        "usage_today": auth.usage_today(db, user),
        "free_daily_quota": auth.FREE_DAILY_QUOTA,
        "billing_configured": billing.billing_configured(),
    }


# --------------------------------------------------------------------------
# Core lead lookup
# --------------------------------------------------------------------------
class LeadRequest(BaseModel):
    company_name: str = ""
    domain: str = ""
    location_hint: str = ""
    attempt_smtp: bool = True


def _serialize_leads(result: dict) -> dict:
    return {
        "domain": result["domain"],
        "resolved_via": result["resolved_via"],
        "pages_crawled": result["pages_crawled"],
        "warnings": result["warnings"],
        "leads": [
            {
                "name": lead.person.name if lead.person else None,
                "title": lead.person.title if lead.person else None,
                "best_email": lead.emails[0].email if lead.emails else None,
                "confidence": round(lead.emails[0].confidence, 2) if lead.emails else 0,
                "alt_emails": [e.email for e in lead.emails[1:]],
                "source_urls": lead.source_urls,
                "notes": lead.emails[0].notes if lead.emails else [],
            }
            for lead in result["leads"]
        ],
    }


@app.post("/leads")
def leads_endpoint(req: LeadRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not req.company_name and not req.domain:
        raise HTTPException(status_code=400, detail="Provide company_name or domain")
    auth.check_and_increment_quota(db, user, cost=1)
    result = generate_leads(
        company_name=req.company_name, domain=req.domain,
        location_hint=req.location_hint, attempt_smtp=req.attempt_smtp,
        use_llm=user.is_paid,  # paid tier gets the higher-accuracy LLM extraction pass automatically
    )
    return _serialize_leads(result)


class BulkLeadRequest(BaseModel):
    companies: list  # list[str] — company names or domains, one per entry
    attempt_smtp: bool = False  # default off in bulk mode — much faster, still MX+pattern scored


@app.post("/leads/bulk")
def leads_bulk_endpoint(req: BulkLeadRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not user.is_paid:
        raise HTTPException(status_code=402, detail="Bulk lookup is a paid-tier feature. Upgrade to use it.")
    companies = [c.strip() for c in req.companies if c.strip()]
    if not companies:
        raise HTTPException(status_code=400, detail="Provide at least one company name/domain")
    if len(companies) > auth.PAID_BULK_MAX_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Max {auth.PAID_BULK_MAX_PER_REQUEST} companies per request — split into batches.",
        )

    results = []
    for entry in companies:
        is_domain = "." in entry and " " not in entry
        try:
            result = generate_leads(
                domain=entry if is_domain else "",
                company_name="" if is_domain else entry,
                attempt_smtp=req.attempt_smtp,
                use_llm=user.is_paid,
            )
            results.append({"input": entry, **_serialize_leads(result)})
        except Exception as e:
            results.append({"input": entry, "error": str(e), "leads": []})

    return {"count": len(results), "results": results}


# --------------------------------------------------------------------------
# Billing
# --------------------------------------------------------------------------
@app.post("/billing/create-checkout-session")
def create_checkout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    url = billing.create_checkout_session(db, user)
    return {"checkout_url": url}


@app.post("/billing/webhook")
async def dodo_webhook(
    request: Request,
    webhook_id: str = Header(default="", alias="webhook-id"),
    webhook_signature: str = Header(default="", alias="webhook-signature"),
    webhook_timestamp: str = Header(default="", alias="webhook-timestamp"),
    db: Session = Depends(get_db),
):
    payload = await request.body()
    headers = {
        "webhook-id": webhook_id,
        "webhook-signature": webhook_signature,
        "webhook-timestamp": webhook_timestamp,
    }
    return billing.handle_webhook(db, payload, headers)


@app.get("/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
def index():
    return FileResponse("frontend/index.html")
