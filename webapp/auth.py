"""Password hashing + user/API-key management. Deliberately simple: API-key
bearer auth (one key per signup) rather than JWTs/sessions — it's the right
model for a product whose primary consumer is "hit our API", and the same
key works for both the web frontend and any script/integration a paying
customer wires up later.
"""
from datetime import date, datetime, timedelta

import bcrypt
from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from .db import User, ApiKey, UsageEvent, PasswordResetToken, new_api_key, new_reset_token

FREE_DAILY_QUOTA = 5
FREE_BULK_MAX = 0          # bulk lookups are a paid-only feature
PAID_BULK_MAX_PER_REQUEST = 50  # sanity cap even for paid, so one request can't hang forever
FREE_CATEGORY_MAX = 3    # free tier gets a taste of category/niche search, capped small


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_user(db: Session, email: str, password: str) -> tuple:
    email = email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.flush()
    api_key = ApiKey(key=new_api_key(), user_id=user.id)
    db.add(api_key)
    db.commit()
    db.refresh(user)
    return user, api_key.key


def authenticate(db: Session, email: str, password: str) -> User:
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return user


def get_active_api_key(db: Session, user: User) -> str:
    key = db.query(ApiKey).filter(ApiKey.user_id == user.id, ApiKey.revoked.is_(False)).first()
    if not key:
        key = ApiKey(key=new_api_key(), user_id=user.id)
        db.add(key)
        db.commit()
    return key.key


def get_user_from_api_key(db: Session, api_key: str) -> User:
    row = db.query(ApiKey).filter(ApiKey.key == api_key, ApiKey.revoked.is_(False)).first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    return row.user


def require_user(authorization: str = Header(default=""), db: Session = None) -> User:
    """FastAPI dependency: expects 'Authorization: Bearer <api_key>'."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <api_key> header")
    api_key = authorization.split(" ", 1)[1].strip()
    return get_user_from_api_key(db, api_key)


def check_and_increment_quota(db: Session, user: User, cost: int = 1):
    if user.is_paid:
        return  # unlimited on paid tier
    today = date.today()
    row = db.query(UsageEvent).filter(UsageEvent.user_id == user.id, UsageEvent.day == today).first()
    used = row.count if row else 0
    if used + cost > FREE_DAILY_QUOTA:
        raise HTTPException(
            status_code=402,
            detail=f"Free tier limit of {FREE_DAILY_QUOTA} lookups/day reached. Upgrade for unlimited.",
        )
    if row:
        row.count += cost
    else:
        db.add(UsageEvent(user_id=user.id, day=today, count=cost))
    db.commit()


def usage_today(db: Session, user: User) -> int:
    today = date.today()
    row = db.query(UsageEvent).filter(UsageEvent.user_id == user.id, UsageEvent.day == today).first()
    return row.count if row else 0


RESET_TOKEN_TTL_MINUTES = 30


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.strip().lower()).first()


def create_password_reset(db: Session, user: User) -> str:
    token = new_reset_token()
    expires = datetime.utcnow() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
    db.add(PasswordResetToken(user_id=user.id, token=token, expires_at=expires))
    db.commit()
    return token


def consume_password_reset(db: Session, token: str, new_password: str) -> User:
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token == token, PasswordResetToken.used.is_(False))
        .first()
    )
    if not row or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")
    user = db.query(User).filter(User.id == row.user_id).first()
    user.password_hash = hash_password(new_password)
    row.used = True
    db.commit()
    db.refresh(user)
    return user
