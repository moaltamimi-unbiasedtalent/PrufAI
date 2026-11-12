from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class EnrollmentMessage:
    purpose: Literal["invitation", "password_reset"]
    recipient: str
    token: str = field(repr=False)
    organisation_id: UUID | None = None


class EnrollmentDelivery(Protocol):
    """The application creates tokens, then hands them to an explicitly wired adapter."""

    def send(self, message: EnrollmentMessage) -> None: ...


def enrollment_delivery() -> EnrollmentDelivery | None:
    """No delivery is configured in the product. Disposable harnesses may inject one."""
    return None
