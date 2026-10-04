import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def is_valid_token(candidate: str | None, settings: Settings) -> bool:
    if not candidate:
        return False
    return any(
        hmac.compare_digest(candidate.encode(), token.encode()) for token in settings.client_tokens
    )


def require_client(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """Accept a request only if it carries one of the configured client tokens."""
    if credentials is not None and is_valid_token(credentials.credentials, settings):
        return credentials.credentials
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing client token",
        headers={"WWW-Authenticate": "Bearer"},
    )
