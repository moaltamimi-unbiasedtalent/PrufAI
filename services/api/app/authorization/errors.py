class AuthorizationError(PermissionError):
    """Raised when an identity lacks the required scoped authority."""
