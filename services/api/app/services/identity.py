from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.models.enums import UserStatus


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("email must contain a valid address shape")
    return normalized


class IdentityService:
    """Persistence operations for identities; credential authentication is deferred."""

    @staticmethod
    def create_user(
        session: Session,
        *,
        email: str,
        display_name: Optional[str] = None,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> User:
        normalized_email = normalize_email(email)
        existing = session.scalar(select(User).where(User.normalized_email == normalized_email))
        if existing is not None:
            raise ValueError("normalized email already exists")
        user = User(
            email=email.strip(),
            normalized_email=normalized_email,
            display_name=display_name,
            status=status,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
