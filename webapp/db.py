"""Persistence layer. Defaults to a local SQLite file so the whole product
runs at $0 with no external service — set DATABASE_URL to point at real
Postgres when you're ready to scale (SQLAlchemy handles both identically).
"""
import os
import secrets
from datetime import date, datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, Date, DateTime,
    ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./lead_engine.db")
# Render (and some other providers) hand out "postgres://" URLs, but SQLAlchemy's
# create_engine only recognizes the "postgresql://" dialect prefix - normalize it.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_paid = Column(Boolean, default=False, nullable=False)
    dodo_customer_id = Column(String, nullable=True)
    dodo_subscription_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    usage_events = relationship("UsageEvent", back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="api_keys")


class UsageEvent(Base):
    """One row per (user, day) with a running count — the free-tier quota ledger."""
    __tablename__ = "usage_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    day = Column(Date, default=date.today, nullable=False)
    count = Column(Integer, default=0, nullable=False)

    user = relationship("User", back_populates="usage_events")
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_user_day"),)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def new_api_key() -> str:
    return "le_" + secrets.token_urlsafe(32)
