from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError


@dataclass(frozen=True)
class PasswordPolicy:
    minimum_length: int = 12
    maximum_length: int = 128

    def validate(self, password: str) -> None:
        if not isinstance(password, str):
            raise ValueError("password must be text")
        length = len(password)
        if length < self.minimum_length:
            raise ValueError(f"password must be at least {self.minimum_length} characters")
        if length > self.maximum_length:
            raise ValueError(f"password must be at most {self.maximum_length} characters")
        if "\x00" in password:
            raise ValueError("password contains an invalid character")


_password_hasher = PasswordHasher()
_password_policy = PasswordPolicy()


def validate_password(password: str) -> None:
    _password_policy.validate(password)


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False
