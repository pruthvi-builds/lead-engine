"""Dodo Payments billing integration (Checkout Sessions + webhooks), using
the official `dodopayments` Python SDK.

Verified against the SDK actually installed (dodopayments 0.x) — the client's
real resource names/methods are `client.checkout_sessions.create(...)` and
`client.webhooks.unwrap(...)`, not the more generic names you might guess
from other providers' docs.

Required env vars (get these from https://app.dodopayments.com — API Keys
and Webhooks sections):

  DODO_PAYMENTS_API_KEY       your secret/bearer API key
  DODO_PAYMENTS_ENVIRONMENT   "test_mode" or "live_mode"
  DODO_PAYMENTS_WEBHOOK_KEY   webhook signing secret, from the webhook you create
  DODO_PRODUCT_ID              the product_id of your subscription product
                                (create it once in the Dodo dashboard)
  PUBLIC_BASE_URL              e.g. https://yourdomain.com (for the return_url)

Until these are set, /billing/create-checkout-session returns a clear 501
instead of crashing — the free tier works fully with zero billing config.
"""
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .db import User

DODO_API_KEY = os.environ.get("DODO_PAYMENTS_API_KEY", "")
DODO_ENVIRONMENT = os.environ.get("DODO_PAYMENTS_ENVIRONMENT", "test_mode")
DODO_WEBHOOK_KEY = os.environ.get("DODO_PAYMENTS_WEBHOOK_KEY", "")
DODO_PRODUCT_ID = os.environ.get("DODO_PRODUCT_ID", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")

# Event types that mean "this user should have paid-tier access right now".
# subscription.on_hold is deliberately excluded from the downgrade set below —
# per Dodo's docs it's a recoverable payment-retry state, not a hard failure,
# so we leave the user on paid tier during the retry window rather than
# yanking access on the first missed charge.
GRANT_EVENTS = {"subscription.active", "subscription.renewed"}
REVOKE_EVENTS = {"subscription.cancelled", "subscription.expired", "subscription.failed"}


def billing_configured() -> bool:
    return bool(DODO_API_KEY and DODO_PRODUCT_ID)


def _get_client():
    from dodopayments import DodoPayments  # imported lazily — only needed once billing is used
    return DodoPayments(
        bearer_token=DODO_API_KEY,
        webhook_key=DODO_WEBHOOK_KEY or None,
        environment=DODO_ENVIRONMENT,
    )


def create_checkout_session(db: Session, user: User) -> str:
    if not billing_configured():
        raise HTTPException(
            status_code=501,
            detail="Billing isn't configured yet — set DODO_PAYMENTS_API_KEY and DODO_PRODUCT_ID "
                   "to enable upgrades. The free tier works fully without this.",
        )
    client = _get_client()

    session = client.checkout_sessions.create(
        product_cart=[{"product_id": DODO_PRODUCT_ID, "quantity": 1}],
        customer={"email": user.email, "name": user.email},
        return_url=f"{PUBLIC_BASE_URL}/?upgraded=1",
    )
    return session.checkout_url


def handle_webhook(db: Session, payload: bytes, headers: dict):
    if not DODO_WEBHOOK_KEY:
        raise HTTPException(status_code=501, detail="Webhook key not configured")

    client = _get_client()
    try:
        event = client.webhooks.unwrap(payload.decode("utf-8"), headers=headers)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Dodo Payments webhook signature")

    etype = getattr(event, "type", None)
    data = getattr(event, "data", None)

    # Only subscription.* events carry the nested subscription/customer object we need.
    # payment.* events are also delivered but we don't need to act on them separately —
    # subscription.active / subscription.renewed already fire alongside a successful charge.
    if data is None or not hasattr(data, "customer"):
        return {"received": True, "type": etype}

    customer_email = getattr(data.customer, "email", None)
    customer_id = getattr(data.customer, "customer_id", None)
    subscription_id = getattr(data, "subscription_id", None)

    user = None
    if customer_id:
        user = db.query(User).filter(User.dodo_customer_id == customer_id).first()
    if not user and customer_email:
        user = db.query(User).filter(User.email == customer_email.lower()).first()

    if not user:
        return {"received": True, "type": etype, "note": "no matching user"}

    if etype in GRANT_EVENTS:
        user.is_paid = True
        user.dodo_customer_id = customer_id or user.dodo_customer_id
        user.dodo_subscription_id = subscription_id or user.dodo_subscription_id
        db.commit()
    elif etype in REVOKE_EVENTS:
        user.is_paid = False
        db.commit()

    return {"received": True, "type": etype}
