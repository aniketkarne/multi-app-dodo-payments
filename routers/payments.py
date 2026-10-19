"""
Payment Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
import json

from services.database import (
    get_db, CheckoutSession, Payment, ProductCatalog, AppRegistry,
    CheckoutStatus, PaymentStatus
)
from services.dodo_service import get_dodo_service, DodoPaymentsService
from services.credit_service import CreditService
from services.webhook_service import WebhookService
from middleware.auth import get_current_app_name
from utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


# Request/Response Models
class CheckoutRequest(BaseModel):
    app_name: str = Field(..., description="Application name")
    user_id: str = Field(..., description="User identifier")
    product_id: str = Field(..., description="Product ID to purchase")
    customer_email: Optional[str] = Field(None, description="Customer email")
    extra_data: Optional[Dict[str, Any]] = Field(default_factory=dict)


class CheckoutResponse(BaseModel):
    session_id: str
    checkout_url: str
    expires_at: str
    message: str = "Checkout session created successfully"


class VerifyPaymentResponse(BaseModel):
    status: str
    payment_id: Optional[str] = None
    user_id: Optional[str] = None
    product_id: Optional[str] = None
    credits_granted: Optional[int] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    completed_at: Optional[str] = None


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
        request: CheckoutRequest,
        db: Session = Depends(get_db),
        app_name: str = Depends(get_current_app_name)
):
    """
    Create a Dodo Payments checkout session

    Flow:
    1. Validate app and product
    2. Create Dodo checkout session
    3. Save session to database
    4. Return checkout URL
    """
    # Verify app_name matches authenticated app
    if request.app_name != app_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="App name mismatch"
        )

    # Get app configuration
    app = db.query(AppRegistry).filter(
        AppRegistry.app_name == request.app_name,
        AppRegistry.is_active == True
    ).first()

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="App not found or inactive"
        )

    # Get product
    product = db.query(ProductCatalog).filter(
        ProductCatalog.app_name == request.app_name,
        ProductCatalog.product_id == request.product_id,
        ProductCatalog.is_active == True
    ).first()

    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or inactive"
        )

    # Prepare return URL (Dodo Payments uses a single return_url)
    # Dodo will add: ?payment_id=xxx&status=succeeded to this URL
    # NOTE: Dodo does NOT support placeholder replacement like {CHECKOUT_SESSION_ID}
    return_url = request.extra_data.get("return_url") if request.extra_data else None
    if not return_url:
        # Don't add any query params - Dodo will add payment_id and status
        return_url = app.success_redirect_base

    # Create Dodo checkout session
    dodo_service = get_dodo_service()

    try:
        dodo_response = dodo_service.create_checkout_session(
            amount=float(product.price),
            currency=product.currency,
            product_name=product.product_name,
            product_description=product.description or "",
            return_url=return_url,
            customer_email=request.customer_email,
            metadata={
                "app_name": request.app_name,
                "user_id": request.user_id,
                "product_id": request.product_id,
                "credits_granted": product.credits_granted,
                **request.extra_data
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create checkout: {str(e)}"
        )

    # Save checkout session to database
    checkout_session = CheckoutSession(
        session_id=dodo_response["session_id"],
        app_name=request.app_name,
        user_id=request.user_id,
        product_id=request.product_id,
        amount=product.price,
        currency=product.currency,
        status=CheckoutStatus.PENDING,
        customer_email=request.customer_email,
        success_url=return_url,  # Store return_url as success_url for backwards compatibility
        cancel_url=app.cancel_redirect_base if hasattr(app, 'cancel_redirect_base') else return_url,
        checkout_url=dodo_response["checkout_url"],
        expires_at=datetime.fromisoformat(dodo_response["expires_at"].replace('Z', '+00:00')),
        extra_data=request.extra_data
    )

    db.add(checkout_session)
    db.commit()

    logger.info(
        f"Checkout session created: {request.app_name}/{request.user_id}",
        extra={
            'extra_fields': {
                'app_name': request.app_name,
                'user_id': request.user_id,
                'product_id': request.product_id,
                'session_id': dodo_response["session_id"],
                'amount': float(product.price),
                'currency': product.currency
            }
        }
    )

    return CheckoutResponse(
        session_id=dodo_response["session_id"],
        checkout_url=dodo_response["checkout_url"],
        expires_at=dodo_response["expires_at"]
    )


@router.get("/verify/{identifier}", response_model=VerifyPaymentResponse)
async def verify_payment(
        identifier: str,
        db: Session = Depends(get_db),
        app_name: str = Depends(get_current_app_name)
):
    """
    Verify payment/subscription status using payment_id or subscription_id

    Dodo redirects:
    - One-time payment: return_url?payment_id=xxx&status=succeeded
    - Subscription: return_url?subscription_id=xxx&status=active

    Use this endpoint with either ID to get full details
    """
    # Try to find payment by payment_id first
    payment = db.query(Payment).filter(
        Payment.payment_id == identifier,
        Payment.app_name == app_name
    ).first()

    # If not found, try subscription_id
    if not payment:
        payment = db.query(Payment).filter(
            Payment.subscription_id == identifier,
            Payment.app_name == app_name
        ).first()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment or subscription not found"
        )

    # Get checkout session
    checkout = db.query(CheckoutSession).filter(
        CheckoutSession.session_id == payment.session_id
    ).first()

    return VerifyPaymentResponse(
        status=payment.status.value,
        payment_id=payment.payment_id,
        user_id=payment.user_id,
        product_id=payment.product_id,
        credits_granted=payment.credits_granted,
        amount=float(payment.amount),
        currency=payment.currency,
        completed_at=checkout.completed_at.isoformat() if checkout and checkout.completed_at else None
    )


@router.post("/webhook")
async def handle_webhook(
        request: Request,
        db: Session = Depends(get_db),
        webhook_signature: Optional[str] = Header(None, alias="webhook-signature"),
        webhook_id: Optional[str] = Header(None, alias="webhook-id"),
        webhook_timestamp: Optional[str] = Header(None, alias="webhook-timestamp")
):
    """
    Handle Dodo Payments webhook (uses Svix for webhook delivery)

    Events:
    - payment.succeeded
    - payment.failed
    - subscription.renewed
    - subscription.cancelled
    """
    # Get raw body for signature verification
    body = await request.body()
    body_str = body.decode('utf-8')

    # Parse JSON from body string
    payload = json.loads(body_str)

    # Validate ALL required Svix headers (security + idempotency)
    if not webhook_id or not webhook_timestamp or not webhook_signature:
        logger.error(
            "Missing required Svix webhook headers",
            extra={
                'extra_fields': {
                    'has_webhook_id': bool(webhook_id),
                    'has_webhook_timestamp': bool(webhook_timestamp),
                    'has_webhook_signature': bool(webhook_signature)
                }
            }
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required webhook headers: webhook-id, webhook-timestamp, and webhook-signature are required"
        )

    # Verify Svix webhook signature (MANDATORY - no bypassing)
    # Svix signature format: signed_content = "${webhook_id}.${webhook_timestamp}.${body}"
    dodo_service = get_dodo_service()
    signed_content = f"{webhook_id}.{webhook_timestamp}.{body_str}"

    # Extract signature (format: "v1,<signature>")
    sig_parts = webhook_signature.split(',')
    if len(sig_parts) != 2 or sig_parts[0] != 'v1':
        logger.warning(
            "Invalid webhook signature format",
            extra={
                'extra_fields': {
                    'webhook_id': webhook_id,
                    'signature_format': webhook_signature[:50]
                }
            }
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature format"
        )

    actual_signature = sig_parts[1]
    signature_verified = dodo_service.verify_webhook_signature(
        signed_content,
        actual_signature
    )

    if not signature_verified:
        logger.warning(
            "Invalid webhook signature - verification failed",
            extra={
                'extra_fields': {
                    'webhook_id': webhook_id,
                    'event_type': payload.get("type")
                }
            }
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature"
        )

    # Extract event details
    # Use webhook-id header as event_id for idempotency (Svix standard)
    # webhook_id is guaranteed to exist due to validation above
    event_id = webhook_id
    event_type = payload.get("type") or payload.get("event_type", "unknown")

    # Extract session/payment/subscription data from payload
    # Dodo sends: checkout_session_id, payment_id, subscription_id in the data object
    session_id = payload.get("data", {}).get("checkout_session_id") or payload.get("data", {}).get("session_id")
    payment_id = payload.get("data", {}).get("payment_id")
    subscription_id = payload.get("data", {}).get("subscription_id")

    # Log webhook event
    webhook_service = WebhookService(db)
    event = await webhook_service.log_webhook_event(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        signature=webhook_signature or "",
        signature_verified=signature_verified,
        session_id=session_id,
        payment_id=payment_id,
        subscription_id=subscription_id
    )

    # If already processed (idempotency), return success
    if event.status.value == "PROCESSED":
        return {"status": "already_processed", "event_id": event_id}

    # Extract user_id from metadata for tracking
    metadata = payload.get("data", {}).get("metadata", {})
    user_id = metadata.get("user_id")
    app_name = metadata.get("app_name")

    # Log webhook received (with user_id for Mixpanel tracking)
    if user_id:
        logger.info(
            f"Webhook received: {event_type}",
            extra={
                'extra_fields': {
                    'user_id': user_id,
                    'app_name': app_name,
                    'event_type': event_type,
                    'event_id': event_id,
                    'session_id': session_id,
                    'payment_id': payment_id,
                    'subscription_id': subscription_id,
                    'signature_verified': signature_verified
                }
            }
        )

    # Mark as processing
    webhook_service.mark_event_processing(event_id)

    try:
        # Handle different event types
        # Dodo sends: payment.succeeded, payment.failed, subscription.renewed, etc.
        if event_type in ["payment.succeeded", "payment.success", "checkout.session.completed"]:
            await _handle_payment_success(db, payload, session_id)
        elif event_type == "payment.failed":
            await _handle_payment_failed(db, payload, session_id)
        elif event_type == "subscription.renewed":
            await _handle_subscription_renewed(db, payload)
        elif event_type == "subscription.cancelled":
            await _handle_subscription_cancelled(db, payload)
        elif event_type == "subscription.failed":
            await _handle_subscription_failed(db, payload)

        # Mark as processed
        webhook_service.mark_event_processed(event_id)

        return {"status": "success", "event_id": event_id}

    except Exception as e:
        # Mark as failed
        webhook_service.mark_event_failed(event_id, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook processing failed: {str(e)}"
        )


async def _handle_payment_success(db: Session, payload: Dict[str, Any], session_id: str):
    """Handle successful payment"""
    # Get checkout session first to get user_id
    checkout = db.query(CheckoutSession).filter(
        CheckoutSession.session_id == session_id
    ).first()

    if not checkout:
        logger.error(
            f"Checkout session not found: {session_id}",
            extra={'extra_fields': {'session_id': session_id}}
        )
        raise Exception(f"Checkout session not found: {session_id}")

    # Log payment verification started (distinct from completion)
    logger.info(
        f"Payment verification started for session: {session_id}",
        extra={
            'extra_fields': {
                'session_id': session_id,
                'user_id': checkout.user_id,
                'app_name': checkout.app_name,
                'stage': 'verification'
            }
        }
    )

    # Extract payment_id
    payment_id = payload.get("data", {}).get("payment_id") or f"pay_{uuid.uuid4().hex[:16]}"

    # Check if payment already exists (idempotency)
    existing_payment = db.query(Payment).filter(
        Payment.payment_id == payment_id
    ).first()

    if existing_payment:
        # Payment already processed, skip
        return

    # Get product details
    product = db.query(ProductCatalog).filter(
        ProductCatalog.app_name == checkout.app_name,
        ProductCatalog.product_id == checkout.product_id
    ).first()

    if not product:
        raise Exception(f"Product not found: {checkout.product_id}")

    # Create payment record
    payment = Payment(
        payment_id=payment_id,
        session_id=session_id,
        app_name=checkout.app_name,
        user_id=checkout.user_id,
        product_id=checkout.product_id,
        amount=checkout.amount,
        currency=checkout.currency,
        status=PaymentStatus.SUCCESS,
        payment_method=payload.get("data", {}).get("payment_method"),
        customer_email=checkout.customer_email,
        credits_granted=product.credits_granted,
        dodo_response=payload
    )

    db.add(payment)

    # Update checkout session
    checkout.status = CheckoutStatus.COMPLETED
    checkout.completed_at = datetime.utcnow()

    # Add credits if applicable
    if product.credits_granted > 0:
        credit_service = CreditService(db)
        credit_result = credit_service.add_credits(
            app_name=checkout.app_name,
            user_id=checkout.user_id,
            amount=product.credits_granted,
            payment_id=payment_id,
            description=f"Purchase: {product.product_name}"
        )

        # Update total spent
        from services.database import UserCredit
        from decimal import Decimal
        user_credits = db.query(UserCredit).filter(
            UserCredit.app_name == checkout.app_name,
            UserCredit.user_id == checkout.user_id
        ).first()
        if user_credits:
            user_credits.total_spent += Decimal(str(checkout.amount))

        # Log credits granted as separate event
        logger.info(
            f"Credits granted to user: {product.credits_granted} credits",
            extra={
                'extra_fields': {
                    'user_id': checkout.user_id,
                    'app_name': checkout.app_name,
                    'payment_id': payment_id,
                    'credits_granted': product.credits_granted,
                    'balance_before': credit_result.get('balance_before') if credit_result else None,
                    'balance_after': credit_result.get('balance_after') if credit_result else None,
                    'product_id': checkout.product_id,
                    'product_name': product.product_name
                }
            }
        )

    db.commit()

    logger.info(
        f"Payment processed successfully: {payment_id}",
        extra={
            'extra_fields': {
                'payment_id': payment_id,
                'session_id': session_id,
                'app_name': checkout.app_name,
                'user_id': checkout.user_id,
                'product_id': checkout.product_id,
                'amount': float(checkout.amount),
                'credits_granted': product.credits_granted
            }
        }
    )

    # Send callback to app
    webhook_service = WebhookService(db)
    await webhook_service.send_app_callback(
        app_name=checkout.app_name,
        payload={
            "event": "payment.success",
            "app_name": checkout.app_name,
            "user_id": checkout.user_id,
            "payment_id": payment_id,
            "session_id": session_id,
            "product_id": checkout.product_id,
            "credits_granted": product.credits_granted,
            "amount": float(checkout.amount),
            "currency": checkout.currency,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


async def _handle_payment_failed(db: Session, payload: Dict[str, Any], session_id: str):
    """Handle failed payment - create audit record"""
    # Get checkout session first to get user_id
    checkout = db.query(CheckoutSession).filter(
        CheckoutSession.session_id == session_id
    ).first()

    if not checkout:
        logger.error(
            f"Checkout session not found for failed payment: {session_id}",
            extra={'extra_fields': {'session_id': session_id}}
        )
        return

    # Log payment failure verification (distinct from recording)
    logger.warning(
        f"Payment failure detected for session: {session_id}",
        extra={
            'extra_fields': {
                'session_id': session_id,
                'user_id': checkout.user_id,
                'app_name': checkout.app_name,
                'stage': 'verification'
            }
        }
    )

    # Extract payment_id
    payment_id = payload.get("data", {}).get("payment_id") or f"pay_failed_{uuid.uuid4().hex[:16]}"

    # Check if failure already recorded (idempotency)
    existing_payment = db.query(Payment).filter(
        Payment.payment_id == payment_id
    ).first()

    if existing_payment:
        logger.info(f"Payment failure already recorded: {payment_id}")
        return

    # Create payment record for failed transaction (audit trail)
    payment = Payment(
        payment_id=payment_id,
        session_id=session_id,
        app_name=checkout.app_name,
        user_id=checkout.user_id,
        product_id=checkout.product_id,
        amount=checkout.amount,
        currency=checkout.currency,
        status=PaymentStatus.FAILED,
        payment_method=payload.get("data", {}).get("payment_method"),
        customer_email=checkout.customer_email,
        credits_granted=0,  # No credits for failed payment
        dodo_response=payload
    )

    db.add(payment)

    # Update checkout session status
    checkout.status = CheckoutStatus.FAILED
    checkout.completed_at = datetime.utcnow()

    db.commit()

    logger.warning(
        f"Payment failure recorded: {payment_id}",
        extra={
            'extra_fields': {
                'payment_id': payment_id,
                'session_id': session_id,
                'app_name': checkout.app_name,
                'user_id': checkout.user_id,
                'amount': float(checkout.amount),
                'reason': payload.get("data", {}).get("error_message", "Unknown")
            }
        }
    )

    # Send callback to app about failure
    webhook_service = WebhookService(db)
    await webhook_service.send_app_callback(
        app_name=checkout.app_name,
        payload={
            "event": "payment.failed",
            "app_name": checkout.app_name,
            "user_id": checkout.user_id,
            "payment_id": payment_id,
            "session_id": session_id,
            "product_id": checkout.product_id,
            "amount": float(checkout.amount),
            "currency": checkout.currency,
            "error_message": payload.get("data", {}).get("error_message"),
            "timestamp": datetime.utcnow().isoformat()
        }
    )


async def _handle_subscription_renewed(db: Session, payload: Dict[str, Any]):
    """Handle subscription renewal/creation"""
    data = payload.get("data", {})
    metadata = data.get("metadata", {})

    subscription_id = data.get("subscription_id")
    if not subscription_id:
        raise Exception("No subscription_id in webhook payload")

    app_name = metadata.get("app_name")
    user_id = metadata.get("user_id")
    product_id = metadata.get("product_id")

    if not all([app_name, user_id, product_id]):
        raise Exception("Missing required metadata: app_name, user_id, or product_id")

    # Log subscription renewal verification started
    logger.info(
        f"Subscription renewal verification started: {subscription_id}",
        extra={
            'extra_fields': {
                'user_id': user_id,
                'app_name': app_name,
                'subscription_id': subscription_id,
                'product_id': product_id,
                'status': data.get('status'),
                'stage': 'verification'
            }
        }
    )

    # Check if subscription payment already exists (idempotency)
    existing_payment = db.query(Payment).filter(
        Payment.subscription_id == subscription_id,
        Payment.app_name == app_name
    ).first()

    if existing_payment:
        # Already processed
        return

    # Get product details
    product = db.query(ProductCatalog).filter(
        ProductCatalog.app_name == app_name,
        ProductCatalog.product_id == product_id
    ).first()

    if not product:
        raise Exception(f"Product not found: {product_id}")

    # Get or find checkout session (may not exist for renewals)
    checkout = None
    customer = data.get("customer", {})

    # Create payment record for subscription
    from decimal import Decimal
    amount = Decimal(str(data.get("recurring_pre_tax_amount", 0))) / Decimal("100")  # Convert cents to dollars

    # Dodo occasionally sends subscription webhooks with missing or non-string
    # timestamp fields (older accounts, paused subscriptions, partial renewals).
    # A naive `data.get("created_at").replace(...)` raises AttributeError on None
    # and the whole handler 500s -- which Svix then retries indefinitely. Parse
    # defensively and skip the renewal record when the field is unusable.
    def _parse_iso(value):
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None

    subscription_start = _parse_iso(data.get("created_at"))
    subscription_end = _parse_iso(data.get("expires_at")) if data.get("expires_at") else None

    if subscription_start is None:
        logger.warning(
            f"Subscription renewal missing created_at; recording without period boundaries: {subscription_id}",
            extra={
                'extra_fields': {
                    'subscription_id': subscription_id,
                    'app_name': app_name,
                    'user_id': user_id
                }
            }
        )

    payment = Payment(
        payment_id=f"sub_pay_{uuid.uuid4().hex[:16]}",  # Generate payment_id for subscription
        session_id=None,  # Subscriptions may not have session_id
        app_name=app_name,
        user_id=user_id,
        product_id=product_id,
        amount=amount,
        currency=data.get("currency", "USD"),
        status=PaymentStatus.SUCCESS,
        payment_method="subscription",
        customer_email=customer.get("email") or metadata.get("customer_email"),
        credits_granted=0,  # Subscriptions don't grant credits
        subscription_id=subscription_id,
        subscription_start=subscription_start,
        subscription_end=subscription_end,
        dodo_response=payload
    )

    db.add(payment)
    db.commit()

    # Log subscription activated/renewed successfully
    logger.info(
        f"Subscription renewed successfully: {subscription_id}",
        extra={
            'extra_fields': {
                'user_id': user_id,
                'app_name': app_name,
                'subscription_id': subscription_id,
                'product_id': product_id,
                'product_name': product.product_name,
                'amount': float(amount),
                'currency': data.get("currency", "USD"),
                'status': data.get("status"),
                'next_billing_date': data.get("next_billing_date"),
                'payment_method': 'subscription'
            }
        }
    )

    # Send callback to app
    webhook_service = WebhookService(db)
    await webhook_service.send_app_callback(
        app_name=app_name,
        payload={
            "event": "subscription.active",
            "app_name": app_name,
            "user_id": user_id,
            "subscription_id": subscription_id,
            "product_id": product_id,
            "amount": float(amount),
            "currency": data.get("currency", "USD"),
            "status": data.get("status"),
            "next_billing_date": data.get("next_billing_date"),
            "timestamp": datetime.utcnow().isoformat()
        }
    )


async def _handle_subscription_cancelled(db: Session, payload: Dict[str, Any]):
    """Handle subscription cancellation"""
    data = payload.get("data", {})
    subscription_id = data.get("subscription_id")

    if not subscription_id:
        return

    # Find the subscription payment to get user_id
    payment = db.query(Payment).filter(
        Payment.subscription_id == subscription_id
    ).first()

    if payment:
        # Log subscription cancellation detected
        logger.info(
            f"Subscription cancellation detected: {subscription_id}",
            extra={
                'extra_fields': {
                    'subscription_id': subscription_id,
                    'user_id': payment.user_id,
                    'app_name': payment.app_name,
                    'product_id': payment.product_id,
                    'cancelled_at': data.get("cancelled_at"),
                    'stage': 'detected'
                }
            }
        )

        # Send callback to app about cancellation
        webhook_service = WebhookService(db)
        await webhook_service.send_app_callback(
            app_name=payment.app_name,
            payload={
                "event": "subscription.cancelled",
                "app_name": payment.app_name,
                "user_id": payment.user_id,
                "subscription_id": subscription_id,
                "product_id": payment.product_id,
                "cancelled_at": data.get("cancelled_at"),
                "timestamp": datetime.utcnow().isoformat()
            }
        )


async def _handle_subscription_failed(db: Session, payload: Dict[str, Any]):
    """Handle subscription failure - create audit record"""
    data = payload.get("data", {})
    metadata = data.get("metadata", {})

    subscription_id = data.get("subscription_id")
    if not subscription_id:
        logger.error("No subscription_id in subscription.failed webhook")
        return

    app_name = metadata.get("app_name")
    user_id = metadata.get("user_id")
    product_id = metadata.get("product_id")

    if not all([app_name, user_id, product_id]):
        logger.error(
            f"Missing metadata in subscription.failed webhook",
            extra={'extra_fields': {'subscription_id': subscription_id}}
        )
        return

    # Log subscription failure detected (with user_id for Mixpanel)
    logger.warning(
        f"Subscription failure detected: {subscription_id}",
        extra={
            'extra_fields': {
                'subscription_id': subscription_id,
                'user_id': user_id,
                'app_name': app_name,
                'product_id': product_id,
                'stage': 'detected'
            }
        }
    )

    # Check if failure already recorded (idempotency)
    existing_payment = db.query(Payment).filter(
        Payment.subscription_id == subscription_id,
        Payment.status == PaymentStatus.FAILED
    ).first()

    if existing_payment:
        logger.info(f"Subscription failure already recorded: {subscription_id}")
        return

    # Get product details for amount
    product = db.query(ProductCatalog).filter(
        ProductCatalog.app_name == app_name,
        ProductCatalog.product_id == product_id
    ).first()

    amount = 0
    currency = "USD"
    if product:
        amount = product.price
        currency = product.currency

    # Create payment record for failed subscription (audit trail)
    from decimal import Decimal
    payment = Payment(
        payment_id=f"sub_pay_failed_{uuid.uuid4().hex[:16]}",
        session_id=None,  # Subscriptions don't have session
        app_name=app_name,
        user_id=user_id,
        product_id=product_id,
        amount=Decimal(str(amount)),
        currency=currency,
        status=PaymentStatus.FAILED,
        payment_method="subscription",
        customer_email=data.get("customer", {}).get("email") or metadata.get("customer_email"),
        credits_granted=0,  # No credits for failed subscription
        subscription_id=subscription_id,
        dodo_response=payload
    )

    db.add(payment)
    db.commit()

    logger.warning(
        f"Subscription failure recorded: {subscription_id}",
        extra={
            'extra_fields': {
                'subscription_id': subscription_id,
                'app_name': app_name,
                'user_id': user_id,
                'product_id': product_id,
                'reason': data.get("error_message", "Unknown")
            }
        }
    )

    # Send callback to app about failure
    webhook_service = WebhookService(db)
    await webhook_service.send_app_callback(
        app_name=app_name,
        payload={
            "event": "subscription.failed",
            "app_name": app_name,
            "user_id": user_id,
            "subscription_id": subscription_id,
            "product_id": product_id,
            "amount": float(amount),
            "currency": currency,
            "error_message": data.get("error_message"),
            "timestamp": datetime.utcnow().isoformat()
        }
    )

