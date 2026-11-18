"""
App Management Endpoints (Admin)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import secrets
import hashlib

from services.database import get_db, AppRegistry, APIKey
from middleware.auth import hash_api_key, verify_admin_key

router = APIRouter(prefix="/apps", tags=["apps"])


# Request/Response Models
class RegisterAppRequest(BaseModel):
    app_name: str = Field(..., description="Unique app identifier")
    app_display_name: str
    callback_url: Optional[str] = None
    success_redirect_base: Optional[str] = None
    cancel_redirect_base: Optional[str] = None
    credits_enabled: bool = True
    subscription_enabled: bool = False
    currency: str = "USD"
    extra_data: Optional[Dict[str, Any]] = None


class RegisterAppResponse(BaseModel):
    app_name: str
    api_key: str
    app_secret: str
    message: str = "App registered successfully. Store these credentials securely."


class AppInfoResponse(BaseModel):
    app_name: str
    app_display_name: str
    credits_enabled: bool
    subscription_enabled: bool
    currency: str
    is_active: bool
    created_at: str


@router.post("/register", response_model=RegisterAppResponse)
async def register_app(
    request: RegisterAppRequest,
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin_key)
):
    """
    Register a new app with the payment service

    Requires the X-Admin-Key header (see verify_admin_key).
    """
    # Check if app already exists
    existing_app = db.query(AppRegistry).filter(
        AppRegistry.app_name == request.app_name
    ).first()
    
    if existing_app:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="App with this name already exists"
        )
    
    # Generate app secret
    app_secret = f"sk_{request.app_name}_{secrets.token_urlsafe(32)}"
    app_secret_hash = hashlib.sha256(app_secret.encode()).hexdigest()
    
    # Create app registry entry
    app = AppRegistry(
        app_name=request.app_name,
        app_display_name=request.app_display_name,
        app_secret_key=app_secret_hash,
        callback_url=request.callback_url,
        success_redirect_base=request.success_redirect_base,
        cancel_redirect_base=request.cancel_redirect_base,
        credits_enabled=request.credits_enabled,
        subscription_enabled=request.subscription_enabled,
        currency=request.currency,
        extra_data=request.extra_data
    )
    
    db.add(app)
    db.flush()
    
    # Generate API key
    api_key = f"pk_live_{request.app_name}_{secrets.token_urlsafe(32)}"
    api_key_prefix = f"pk_live_{request.app_name[:8]}"
    
    api_key_obj = APIKey(
        app_name=request.app_name,
        api_key=hash_api_key(api_key),
        api_key_prefix=api_key_prefix,
        key_type="LIVE",
        scopes=["payments", "products", "credits"]
    )
    
    db.add(api_key_obj)
    db.commit()
    
    return RegisterAppResponse(
        app_name=request.app_name,
        api_key=api_key,
        app_secret=app_secret
    )


@router.get("/{app_name}", response_model=AppInfoResponse)
async def get_app_info(
    app_name: str,
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin_key)
):
    """Get app information"""
    app = db.query(AppRegistry).filter(
        AppRegistry.app_name == app_name
    ).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    return AppInfoResponse(
        app_name=app.app_name,
        app_display_name=app.app_display_name,
        credits_enabled=app.credits_enabled,
        subscription_enabled=app.subscription_enabled,
        currency=app.currency,
        is_active=app.is_active,
        created_at=app.created_at.isoformat()
    )


@router.put("/{app_name}/deactivate")
async def deactivate_app(
    app_name: str,
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin_key)
):
    """Deactivate an app"""
    app = db.query(AppRegistry).filter(
        AppRegistry.app_name == app_name
    ).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    app.is_active = False
    db.commit()
    
    return {"message": f"App {app_name} deactivated successfully"}


@router.put("/{app_name}/activate")
async def activate_app(
    app_name: str,
    db: Session = Depends(get_db),
    _admin: None = Depends(verify_admin_key)
):
    """Activate an app"""
    app = db.query(AppRegistry).filter(
        AppRegistry.app_name == app_name
    ).first()
    
    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found"
        )
    
    app.is_active = True
    db.commit()
    
    return {"message": f"App {app_name} activated successfully"}

