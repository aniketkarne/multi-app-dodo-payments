"""
Authentication Middleware for API Key validation
"""
import hashlib
import hmac

from fastapi import Request, HTTPException, Depends, status
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from services.database import APIKey, get_db
from config.config import get_settings

security = HTTPBearer(auto_error=False)


def hash_api_key(api_key: str) -> str:
    """
    Derive the stored form of an API key: HMAC-SHA256 keyed with
    API_KEY_ENCRYPTION_KEY.

    Deterministic, so verification can look the row up by equality on the
    hash. The raw key is returned to the caller once at registration and
    never stored.
    """
    key = get_settings().API_KEY_ENCRYPTION_KEY.encode()
    return hmac.new(key, api_key.encode(), hashlib.sha256).hexdigest()


def verify_admin_key(request: Request) -> None:
    """
    Verify the X-Admin-Key header on admin endpoints (/apps).

    Fails closed: if ADMIN_API_KEY is not configured, admin endpoints are
    unreachable rather than open.

    Raises:
        HTTPException: 503 if unconfigured, 401/403 on bad credentials
    """
    settings = get_settings()

    if not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin API is not configured. Set ADMIN_API_KEY to enable /apps endpoints."
        )

    admin_key = request.headers.get("X-Admin-Key")

    if not admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin key. Include X-Admin-Key header."
        )

    if not hmac.compare_digest(admin_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin key"
        )


async def verify_api_key(request: Request, db: Session = Depends(get_db)) -> str:
    """
    Verify API key from X-API-Key header

    Returns:
        app_name if valid

    Raises:
        HTTPException: If API key is invalid
    """
    # Get API key from header
    api_key = request.headers.get("X-API-Key")

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Include X-API-Key header."
        )

    # Validate API key. The row stores the hash; the presented key never
    # leaves this function in raw form.
    try:
        api_key_obj = db.query(APIKey).filter(
            APIKey.api_key == hash_api_key(api_key),
            APIKey.is_active == True
        ).first()

        if not api_key_obj:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key"
            )

        # Check expiration
        if api_key_obj.expires_at and api_key_obj.expires_at < datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired"
            )

        # Update last used timestamp
        api_key_obj.last_used_at = datetime.utcnow()
        db.commit()

        return api_key_obj.app_name

    finally:
        db.close()


async def get_current_app_name(
    request: Request,
    db: Session = Depends(get_db)
) -> str:
    """Dependency to get current app name from API key"""
    return await verify_api_key(request, db)
