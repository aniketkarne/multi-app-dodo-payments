"""
Webhook Processing Service
"""
from sqlalchemy.orm import Session
from typing import Dict, Any
import httpx
import json
from datetime import datetime

from services.database import WebhookEvent, WebhookEventStatus, AppRegistry
from config.config import get_settings
from utils.logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class WebhookService:
    """Service for handling webhook events and callbacks"""
    
    def __init__(self, db: Session):
        self.db = db
    
    async def log_webhook_event(
        self,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        signature: str,
        signature_verified: bool,
        session_id: str = None,
        payment_id: str = None,
        subscription_id: str = None,
        app_name: str = None
    ) -> WebhookEvent:
        """Log incoming webhook event"""
        
        # Check if event already processed (idempotency)
        existing_event = self.db.query(WebhookEvent).filter(
            WebhookEvent.event_id == event_id
        ).first()
        
        if existing_event:
            logger.info(
                f"Webhook event already exists: {event_id}",
                extra={
                    'extra_fields': {
                        'event_id': event_id,
                        'event_type': event_type,
                        'status': existing_event.status.value,
                        'idempotency': 'duplicate'
                    }
                }
            )
            return existing_event
        
        # Create new event log
        event = WebhookEvent(
            event_id=event_id,
            event_type=event_type,
            session_id=session_id,
            payment_id=payment_id,
            subscription_id=subscription_id,
            app_name=app_name,
            status=WebhookEventStatus.RECEIVED,
            payload=payload,
            signature=signature,
            signature_verified=signature_verified
        )
        
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        
        logger.info(
            f"Webhook event logged: {event_type}",
            extra={
                'extra_fields': {
                    'event_id': event_id,
                    'event_type': event_type,
                    'session_id': session_id,
                    'payment_id': payment_id,
                    'subscription_id': subscription_id,
                    'app_name': app_name,
                    'signature_verified': signature_verified
                }
            }
        )
        
        return event
    
    def mark_event_processing(self, event_id: str):
        """Mark event as processing"""
        event = self.db.query(WebhookEvent).filter(
            WebhookEvent.event_id == event_id
        ).first()
        
        if event:
            event.status = WebhookEventStatus.PROCESSING
            self.db.commit()
            logger.debug(
                f"Webhook event marked as processing: {event_id}",
                extra={'extra_fields': {'event_id': event_id}}
            )
    
    def mark_event_processed(self, event_id: str):
        """Mark event as successfully processed"""
        event = self.db.query(WebhookEvent).filter(
            WebhookEvent.event_id == event_id
        ).first()
        
        if event:
            event.status = WebhookEventStatus.PROCESSED
            event.processed_at = datetime.utcnow()
            self.db.commit()
            logger.info(
                f"Webhook event processed successfully: {event_id}",
                extra={
                    'extra_fields': {
                        'event_id': event_id,
                        'event_type': event.event_type
                    }
                }
            )
    
    def mark_event_failed(self, event_id: str, error: str):
        """Mark event as failed"""
        event = self.db.query(WebhookEvent).filter(
            WebhookEvent.event_id == event_id
        ).first()
        
        if event:
            event.status = WebhookEventStatus.FAILED
            event.processing_error = error
            event.retry_count += 1
            self.db.commit()
            logger.error(
                f"Webhook event failed: {event_id}",
                extra={
                    'extra_fields': {
                        'event_id': event_id,
                        'event_type': event.event_type,
                        'error': error,
                        'retry_count': event.retry_count
                    }
                }
            )
    
    async def send_app_callback(
        self,
        app_name: str,
        payload: Dict[str, Any]
    ) -> bool:
        """
        Send webhook callback to app's callback URL
        
        Returns:
            True if successful, False otherwise
        """
        # Get app configuration
        app = self.db.query(AppRegistry).filter(
            AppRegistry.app_name == app_name,
            AppRegistry.is_active == True
        ).first()
        
        if not app or not app.callback_url:
            logger.warning(
                f"No callback URL for app: {app_name}",
                extra={'extra_fields': {'app_name': app_name}}
            )
            return False
        
        try:
            logger.info(
                f"Sending callback to app: {app_name}",
                extra={
                    'extra_fields': {
                        'app_name': app_name,
                        'callback_url': app.callback_url,
                        'event': payload.get('event')
                    }
                }
            )
            
            # Send POST request to app's callback URL. The body is serialized
            # once and sent as those exact bytes -- the signature below covers
            # the wire bytes so receivers can verify against the raw request
            # body (the standard webhook verification in every language).
            body = self._serialize_payload(payload)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    app.callback_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": self._generate_signature(body)
                    }
                )
                
                # Check if successful
                success = response.status_code in [200, 201, 204]
                
                if success:
                    logger.info(
                        f"Callback sent successfully to {app_name}",
                        extra={
                            'extra_fields': {
                                'app_name': app_name,
                                'status_code': response.status_code
                            }
                        }
                    )
                else:
                    logger.warning(
                        f"Callback returned non-success status: {app_name}",
                        extra={
                            'extra_fields': {
                                'app_name': app_name,
                                'status_code': response.status_code,
                                'response': response.text[:500]
                            }
                        }
                    )
                
                return success
                
        except Exception as e:
            logger.error(
                f"Failed to send callback to {app_name}",
                exc_info=True,
                extra={
                    'extra_fields': {
                        'app_name': app_name,
                        'callback_url': app.callback_url if app else None,
                        'error': str(e)
                    }
                }
            )
            return False
    
    @staticmethod
    def _serialize_payload(payload: Dict[str, Any]) -> bytes:
        """Serialize a callback payload into the exact bytes sent on the wire"""
        return json.dumps(payload, sort_keys=True).encode("utf-8")

    @staticmethod
    def _generate_signature(body: bytes) -> str:
        """HMAC-SHA256 (hex) over the raw request body, keyed with WEBHOOK_SIGNING_SECRET"""
        import hashlib
        import hmac

        return hmac.new(
            settings.WEBHOOK_SIGNING_SECRET.encode(),
            body,
            hashlib.sha256
        ).hexdigest()

