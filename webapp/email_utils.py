"""
Minimal transactional email sender via Resend's HTTP API.

The only thing this app ever emails is a password-reset link, so rather
than pull in an SMTP client we make one plain POST request. Set
RESEND_API_KEY (and optionally FROM_EMAIL) in the environment to enable
it; if it's not set, send_email() logs a note and returns False instead
of crashing, so signup/login/lookups keep working even before email is
configured.
"""
import os
import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Lead Engine <onboarding@resend.dev>")


def email_configured() -> bool:
    return bool(RESEND_API_KEY)


def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print(f"[email] RESEND_API_KEY not set - skipping send to {to}: {subject}")
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[email] Resend error {resp.status_code}: {resp.text}")
            return False
        return True
    except Exception as e:
        print(f"[email] send failed: {e}")
        return False
