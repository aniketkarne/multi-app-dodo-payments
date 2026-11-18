"""
Mixpanel Analytics Integration

Track business events and user journey without polluting business logic.
All events are sent asynchronously to avoid blocking operations.

Usage:
    from utils.analytics import track_event, identify_user
    
    track_event(
        user_id="user_123",
        event_name="Payment Succeeded",
        properties={
            "amount": 9.99,
            "currency": "USD",
            "product_id": "pro"
        }
    )
"""
import logging
from typing import Dict, Any, Optional
from threading import Thread
from queue import Queue
from contextvars import ContextVar
from config.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Lazy-loaded Mixpanel client
_mixpanel_client: Optional[Any] = None
_event_queue: Queue = Queue(maxsize=1000)
_worker_thread: Optional[Thread] = None

# Context variable for correlation ID
_correlation_id_ctx: ContextVar[str] = ContextVar('correlation_id', default='no-correlation-id')


def _get_mixpanel_client():
    """Lazy load Mixpanel client"""
    global _mixpanel_client
    
    if _mixpanel_client is None:
        try:
            from mixpanel import Mixpanel, Consumer
            
            # Initialize with EU/India residency support
            if settings.MIXPANEL_DATA_RESIDENCY == "EU":
                consumer = Consumer(api_host="api-eu.mixpanel.com")
            elif settings.MIXPANEL_DATA_RESIDENCY == "IN":
                consumer = Consumer(api_host="api-in.mixpanel.com")
            else:
                consumer = None
            
            _mixpanel_client = Mixpanel(
                settings.MIXPANEL_PROJECT_TOKEN,
                consumer=consumer
            ) if consumer else Mixpanel(settings.MIXPANEL_PROJECT_TOKEN)
            
            logger.info(f"Mixpanel initialized (Residency: {settings.MIXPANEL_DATA_RESIDENCY or 'EU'})")
            
        except ImportError:
            logger.warning("Mixpanel library not installed. Run: pip install mixpanel")
            _mixpanel_client = None
        except Exception as e:
            logger.error(f"Failed to initialize Mixpanel: {e}")
            _mixpanel_client = None
    
    return _mixpanel_client


def _event_worker():
    """Background worker to send events to Mixpanel"""
    while True:
        event_data = _event_queue.get()
        try:
            if event_data is None:  # Poison pill to stop worker
                break

            mp = _get_mixpanel_client()
            if not mp:
                continue

            event_type = event_data.get("type")

            if event_type == "track":
                mp.track(
                    event_data["distinct_id"],
                    event_data["event_name"],
                    event_data["properties"]
                )

            elif event_type == "identify":
                mp.people_set(
                    event_data["distinct_id"],
                    event_data["properties"],
                    meta=event_data.get("meta", {"$ip": 0})
                )

            elif event_type == "increment":
                mp.people_increment(
                    event_data["distinct_id"],
                    event_data["properties"]
                )

        except Exception as e:
            logger.error(f"Mixpanel event worker error: {e}", exc_info=True)
        finally:
            # Always settle the queue item -- a skipped task_done() makes
            # shutdown()'s _event_queue.join() block forever.
            _event_queue.task_done()


def _start_worker():
    """Start background worker thread"""
    global _worker_thread
    
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = Thread(target=_event_worker, daemon=True, name="MixpanelWorker")
        _worker_thread.start()
        logger.info("Mixpanel worker thread started")


def track_event(
    user_id: str,
    event_name: str,
    properties: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None
):
    """
    Track a business event in Mixpanel
    
    Args:
        user_id: User identifier (distinct_id)
        event_name: Event name (e.g. "Payment Succeeded", "Checkout Created")
        properties: Event properties/attributes
        correlation_id: Request correlation ID for tracing
    
    Example:
        track_event(
            user_id="user_123",
            event_name="Payment Succeeded",
            properties={
                "amount": 9.99,
                "currency": "USD",
                "product_id": "pro",
                "payment_method": "card"
            }
        )
    """
    if not settings.MIXPANEL_ENABLED:
        return
    
    try:
        _start_worker()
        
        # Prepare properties (copy: never mutate the caller's dict)
        props = dict(properties or {})
        props["service"] = "payments-microservice"
        props["environment"] = settings.ENVIRONMENT
        
        # Add correlation ID for request tracing
        if correlation_id:
            props["correlation_id"] = correlation_id
        else:
            try:
                props["correlation_id"] = _correlation_id_ctx.get()
            except:
                pass
        
        # Queue event for async processing
        _event_queue.put({
            "type": "track",
            "distinct_id": user_id,
            "event_name": event_name,
            "properties": props
        }, block=False)
        
        logger.debug(f"Mixpanel event queued: {event_name} for user {user_id}")
        
    except Exception as e:
        # Never fail business logic due to analytics
        logger.error(f"Failed to track Mixpanel event: {e}")


def identify_user(
    user_id: str,
    properties: Optional[Dict[str, Any]] = None
):
    """
    Set user profile properties
    
    Args:
        user_id: User identifier
        properties: User profile properties
    
    Example:
        identify_user(
            user_id="user_123",
            properties={
                "email": "user@example.com",
                "plan": "premium",
                "credits_balance": 100
            }
        )
    """
    if not settings.MIXPANEL_ENABLED:
        return
    
    try:
        _start_worker()
        
        _event_queue.put({
            "type": "identify",
            "distinct_id": user_id,
            "properties": properties or {},
            "meta": {"$ip": 0}  # Don't track IP for privacy
        }, block=False)
        
        logger.debug(f"Mixpanel user identified: {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to identify user in Mixpanel: {e}")


def increment_user_property(
    user_id: str,
    properties: Dict[str, int]
):
    """
    Increment numeric user properties
    
    Args:
        user_id: User identifier
        properties: Properties to increment (e.g. {"total_purchases": 1})
    
    Example:
        increment_user_property(
            user_id="user_123",
            properties={
                "total_purchases": 1,
                "lifetime_credits": 60
            }
        )
    """
    if not settings.MIXPANEL_ENABLED:
        return
    
    try:
        _start_worker()
        
        _event_queue.put({
            "type": "increment",
            "distinct_id": user_id,
            "properties": properties
        }, block=False)
        
        logger.debug(f"Mixpanel user property incremented: {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to increment user property in Mixpanel: {e}")


def set_correlation_id(correlation_id: str):
    """Set correlation ID in context for automatic tracking"""
    _correlation_id_ctx.set(correlation_id)


def shutdown():
    """Gracefully shutdown the Mixpanel worker"""
    global _event_queue, _worker_thread
    
    try:
        # Wait for queue to empty
        _event_queue.join()
        
        # Send poison pill to stop worker
        _event_queue.put(None)
        
        # Wait for worker to finish
        if _worker_thread:
            _worker_thread.join(timeout=5)
        
        logger.info("Mixpanel analytics shutdown complete")
        
    except Exception as e:
        logger.error(f"Error during Mixpanel shutdown: {e}")

