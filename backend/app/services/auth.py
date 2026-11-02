import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuthSession, User

password_hash = PasswordHash.recommended()
SESSION_DURATION = timedelta(days=30)
DUMMY_PASSWORD_HASH = password_hash.hash("invalid-login-timing-placeholder")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + SESSION_DURATION,
        )
    )
    return token


def authenticate_session(db: Session, token: str) -> User | None:
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.expires_at > datetime.now(timezone.utc),
        )
    )
    return session.user if session else None


def revoke_session(db: Session, token: str) -> None:
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    if session:
        db.delete(session)
