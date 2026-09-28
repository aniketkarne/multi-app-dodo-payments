"""
Dodo Payments Service Integration
"""
from dodopayments import DodoPayments
from typing import Dict, Any, Optional, Union
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import hmac
import hashlib
from datetime import datetime, timedelta

from config.config import get_settings
from utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


def to_cents(amount: Union[float, int, str, Decimal, None]) -> Optional[int]:
    """Convert a money amount to integer cents, exactly.

    The previous implementation used ``int(amount * 100)`` which silently
    truncates float-precision errors:

        int(29.99 * 100) == 2998  # should be 2999
        int(0.10  * 100) == 9     # borderline
        int(9.99  * 100) == 998   # should be 999

    Dodo charges the integer we send, so an off-by-one cent on every
    ``29.99`` product is a real money bug. This helper uses ``Decimal``
    with ``ROUND_HALF_UP`` and never sees the float trap.

    Returns ``None`` unchanged so the SDK can treat it as "full refund".
    Raises ``ValueError`` on garbage input rather than silently returning 0.
    """
    if amount is None:
        return None
    try:
        # Convert via string to dodge any incoming float imprecision
        # (Decimal(29.99) is fine, but Decimal(some_float) inherits the
        # binary repr — we don't want that).
        if isinstance(amount, float):
            d = Decimal(str(amount))
        elif isinstance(amount, Decimal):
            d = amount
        else:
            d = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"to_cents: cannot parse amount {amount!r}") from e

    cents = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


class DodoPaymentsService:
    """Wrapper for Dodo Payments API"""

    def __init__(self):
        # Initialize Dodo Payments SDK with bearer token
        # Signature: DodoPayments(bearer_token, environment='live_mode'|'test_mode', base_url=None)
        self.client = DodoPayments(
            bearer_token=settings.DODO_API_KEY,
            environment=settings.DODO_ENVIRONMENT,
            base_url=settings.DODO_BASE_URL if settings.DODO_BASE_URL else None
        )

        self.api_key = settings.DODO_API_KEY
        self.base_url = settings.DODO_BASE_URL
        # Use WEBHOOK_SIGNING_SECRET (Dodo's actual env var name)
        self.webhook_secret = settings.WEBHOOK_SIGNING_SECRET

    def create_checkout_session(
            self,
            amount: float,
            currency: str,
            product_name: str,
            product_description: str,
            return_url: str,
            customer_email: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a Dodo checkout session

        Args:
            amount: Amount in the base currency unit (e.g., dollars)
            currency: Currency code (e.g., 'USD')
            product_name: Name of the product
            product_description: Description of the product
            return_url: URL to redirect after payment (supports {CHECKOUT_SESSION_ID} placeholder)
            customer_email: Optional customer email
            metadata: Optional metadata dictionary

        Returns:
            {
                "session_id": str,
                "checkout_url": str,
                "expires_at": str
            }
        """
        try:
            logger.info(
                f"Creating Dodo checkout session",
                extra={
                    'extra_fields': {
                        'product_name': product_name,
                        'amount': amount,
                        'currency': currency,
                        'customer_email': customer_email
                    }
                }
            )

            # Create product cart for checkout
            product_cart = [{
                "product_id": metadata.get("product_id", "custom_product") if metadata else "custom_product",
                "quantity": 1,
                "name": product_name,
                "description": product_description,
                "price": to_cents(amount),  # Exact-cents, no float-trap
                "currency": currency.upper()
            }]

            # Create checkout session with return_url
            # Note: Only product_cart, return_url, and metadata are supported by Dodo Payments
            # customer_email is NOT a supported parameter
            create_params = {
                "product_cart": product_cart,
                "return_url": return_url,
            }

            # Add metadata if provided (can include customer_email inside metadata)
            # NOTE: Dodo Payments requires ALL metadata values to be STRINGS
            if metadata or customer_email:
                create_params["metadata"] = {}

                # Convert all metadata values to strings
                if metadata:
                    for key, value in metadata.items():
                        create_params["metadata"][key] = str(value)

                # Add customer_email to metadata
                if customer_email:
                    create_params["metadata"]["customer_email"] = customer_email

            response = self.client.checkout_sessions.create(**create_params)

            logger.info(
                f"Dodo checkout session created successfully",
                extra={
                    'extra_fields': {
                        'session_id': getattr(response, 'session_id', None) or getattr(response, 'id', None),
                        'amount': amount,
                        'currency': currency
                    }
                }
            )

            # Extract session details from response
            # Try different possible attribute names for the checkout URL
            session_id = getattr(response, 'session_id', None) or getattr(response, 'id', None)

            checkout_url = (
                    getattr(response, 'checkout_url', None) or
                    getattr(response, 'url', None) or
                    getattr(response, 'hosted_url', None) or
                    getattr(response, 'payment_url', None)
            )

            # If still not found, dump the response to see structure
            if not checkout_url:
                response_dict = response.model_dump() if hasattr(response, 'model_dump') else (
                    response.dict() if hasattr(response, 'dict') else {}
                )
                raise Exception(f"Checkout URL not found in response. Response structure: {response_dict}")

            # Calculate expiration (typically 24 hours)
            expires_at = datetime.utcnow() + timedelta(hours=24)

            return {
                "session_id": session_id,
                "checkout_url": checkout_url,
                "expires_at": expires_at.isoformat(),
                "raw_response": response.model_dump() if hasattr(response, 'model_dump') else (
                    response.dict() if hasattr(response, 'dict') else {}
                )
            }

        except Exception as e:
            logger.error(
                f"Failed to create Dodo checkout session",
                exc_info=True,
                extra={
                    'extra_fields': {
                        'product_name': product_name,
                        'amount': amount,
                        'currency': currency,
                        'error': str(e)
                    }
                }
            )
            raise Exception(f"Failed to create Dodo checkout session: {str(e)}")

    def verify_webhook_signature(
            self,
            payload: str,
            signature: str
    ) -> bool:
        """
        Verify Svix webhook signature (used by Dodo Payments)

        Svix secret format: whsec_<base64_encoded_key>
        We need to decode the key before using it for HMAC

        Args:
            payload: Signed content in format: webhook_id.webhook_timestamp.body
            signature: Base64 encoded signature (without 'v1,' prefix)

        Returns:
            True if signature is valid
        """
        try:
            import base64

            # Decode the webhook secret (Svix format: whsec_<base64_key>)
            secret_bytes = self.webhook_secret.encode()

            # If secret starts with 'whsec_', extract and decode the base64 part
            if self.webhook_secret.startswith('whsec_'):
                # Remove 'whsec_' prefix and base64 decode
                base64_part = self.webhook_secret[6:]  # Remove 'whsec_'
                secret_bytes = base64.b64decode(base64_part)

            # Create HMAC signature using the decoded secret
            expected_signature_bytes = hmac.new(
                secret_bytes,
                payload.encode(),
                hashlib.sha256
            ).digest()

            # Encode to base64
            expected_signature = base64.b64encode(expected_signature_bytes).decode()

            # Logging policy: full HMAC material is a forgery primitive if
            # paired with a known payload. At INFO/WARN we only log length
            # + a 8-char prefix. Full sig is DEBUG-only.
            match = expected_signature == signature
            prefix = expected_signature[:8] if expected_signature else ""

            logger.info(
                f"Webhook signature verification "
                f"(expected_len={len(expected_signature)}, "
                f"received_len={len(signature)}, match={match})"
            )
            logger.debug(
                "Webhook signature detail",
                extra={
                    'extra_fields': {
                        'expected_prefix': prefix,
                        'received_prefix': signature[:8] if signature else "",
                        'expected_len': len(expected_signature),
                        'received_len': len(signature),
                        'payload_length': len(payload),
                        'secret_has_prefix': self.webhook_secret.startswith('whsec_'),
                    }
                }
            )

            # Compare signatures
            return hmac.compare_digest(expected_signature, signature)

        except Exception as e:
            logger.error(f"Webhook signature verification error: {e}")
            return False

    def get_payment_details(self, payment_id: str) -> Dict[str, Any]:
        """
        Get payment details from Dodo

        Returns:
            Payment information
        """
        try:
            # This would use Dodo's API to fetch payment details
            # Placeholder for actual implementation
            payment = self.client.payments.retrieve(payment_id)
            return payment.model_dump() if hasattr(payment, 'model_dump') else {}
        except Exception as e:
            raise Exception(f"Failed to get payment details: {str(e)}")

    def create_refund(
            self,
            payment_id: str,
            amount: Optional[float] = None,
            reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a refund for a payment

        Args:
            payment_id: Dodo payment ID
            amount: Refund amount (None for full refund)
            reason: Refund reason

        Returns:
            Refund details
        """
        if not settings.ENABLE_REFUNDS:
            raise Exception("Refunds are not enabled")

        try:
            refund = self.client.refunds.create(
                payment_id=payment_id,
                amount=to_cents(amount),  # Exact-cents; None means full refund
                reason=reason
            )

            return refund.model_dump() if hasattr(refund, 'model_dump') else {}

        except Exception as e:
            raise Exception(f"Failed to create refund: {str(e)}")

    def cancel_subscription(
            self,
            subscription_id: str,
            cancel_at_period_end: bool = True
    ) -> Dict[str, Any]:
        """
        Cancel a subscription

        Args:
            subscription_id: Dodo subscription ID
            cancel_at_period_end: Cancel at end of current period

        Returns:
            Updated subscription details
        """
        if not settings.ENABLE_SUBSCRIPTIONS:
            raise Exception("Subscriptions are not enabled")

        try:
            subscription = self.client.subscriptions.cancel(
                subscription_id=subscription_id,
                cancel_at_period_end=cancel_at_period_end
            )

            return subscription.model_dump() if hasattr(subscription, 'model_dump') else {}

        except Exception as e:
            raise Exception(f"Failed to cancel subscription: {str(e)}")


# Singleton instance
_dodo_service: Optional[DodoPaymentsService] = None


def get_dodo_service() -> DodoPaymentsService:
    """Get Dodo Payments service instance"""
    global _dodo_service
    if _dodo_service is None:
        _dodo_service = DodoPaymentsService()
    return _dodo_service

