"""
Database connection and models for Dodo Payments Microservice
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, Text, JSON, Enum, BigInteger, ForeignKey, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from typing import Generator
import enum

from config.config import get_settings

settings = get_settings()

# Create database engine
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=not settings.is_production
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


# Enums
class CheckoutStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class ProductType(str, enum.Enum):
    ONE_TIME = "ONE_TIME"
    SUBSCRIPTION = "SUBSCRIPTION"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    PAUSED = "PAUSED"


class TransactionType(str, enum.Enum):
    PURCHASE = "PURCHASE"
    DEDUCTION = "DEDUCTION"
    REFUND = "REFUND"
    ADMIN_GRANT = "ADMIN_GRANT"
    BONUS = "BONUS"


class WebhookEventStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


# Models
class AppRegistry(Base):
    __tablename__ = "app_registry"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    app_name = Column(String(100), unique=True, nullable=False)
    app_display_name = Column(String(255), nullable=False)
    app_secret_key = Column(String(255), nullable=False)
    callback_url = Column(String(500))
    success_redirect_base = Column(String(500))
    cancel_redirect_base = Column(String(500))
    user_table_reference = Column(String(255))
    credits_enabled = Column(Boolean, default=True)
    subscription_enabled = Column(Boolean, default=False)
    currency = Column(String(3), default="USD")
    is_active = Column(Boolean, default=True)
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProductCatalog(Base):
    __tablename__ = "product_catalog"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    product_id = Column(String(100), nullable=False)
    product_name = Column(String(255), nullable=False)
    description = Column(Text)
    product_type = Column(Enum(ProductType), nullable=False, default=ProductType.ONE_TIME)
    price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    credits_granted = Column(Integer, default=0)
    subscription_interval = Column(String(20))
    subscription_benefits = Column(JSON)
    is_popular = Column(Boolean, default=False)
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CheckoutSession(Base):
    __tablename__ = "checkout_sessions"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    session_id = Column(String(255), unique=True, nullable=False)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    product_id = Column(String(100), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(Enum(CheckoutStatus), default=CheckoutStatus.PENDING)
    payment_method = Column(String(50))
    customer_email = Column(String(255))
    success_url = Column(String(500))
    cancel_url = Column(String(500))
    checkout_url = Column(String(500))
    expires_at = Column(DateTime)
    completed_at = Column(DateTime)
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    payment_id = Column(String(255), unique=True, nullable=False)
    session_id = Column(String(255), ForeignKey("checkout_sessions.session_id", ondelete="CASCADE"), nullable=True)  # Nullable for subscriptions
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    product_id = Column(String(100), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(Enum(PaymentStatus), default=PaymentStatus.SUCCESS)
    payment_method = Column(String(50))
    customer_email = Column(String(255))
    credits_granted = Column(Integer, default=0)
    subscription_id = Column(String(255))
    subscription_start = Column(DateTime)
    subscription_end = Column(DateTime)
    dodo_response = Column(JSON)
    refund_amount = Column(Numeric(10, 2), default=0.00)
    refund_reason = Column(Text)
    refunded_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id = Column(String(255), unique=True, nullable=False)
    event_type = Column(String(100), nullable=False)
    session_id = Column(String(255))
    payment_id = Column(String(255))
    subscription_id = Column(String(255))  # Track subscription webhooks
    app_name = Column(String(100))
    status = Column(Enum(WebhookEventStatus), default=WebhookEventStatus.RECEIVED)
    payload = Column(JSON, nullable=False)
    signature = Column(String(500))
    signature_verified = Column(Boolean, default=False)
    processing_error = Column(Text)
    retry_count = Column(Integer, default=0)
    processed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    subscription_id = Column(String(255), unique=True, nullable=False)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    product_id = Column(String(100), nullable=False)
    status = Column(Enum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE)
    interval_type = Column(String(20), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    current_period_start = Column(DateTime)
    current_period_end = Column(DateTime)
    cancel_at_period_end = Column(Boolean, default=False)
    cancelled_at = Column(DateTime)
    cancellation_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserCredit(Base):
    __tablename__ = "user_credits"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    credit_balance = Column(Integer, nullable=False, default=0)
    total_credits_purchased = Column(Integer, default=0)
    total_credits_used = Column(Integer, default=0)
    total_spent = Column(Numeric(10, 2), default=0.00)
    last_purchase_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    transaction_id = Column(String(100), unique=True, nullable=False)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    type = Column(Enum(TransactionType), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_before = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    payment_id = Column(String(255))
    description = Column(String(500))
    extra_data = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)


class Refund(Base):
    __tablename__ = "refunds"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    refund_id = Column(String(255), unique=True, nullable=False)
    payment_id = Column(String(255), ForeignKey("payments.payment_id", ondelete="CASCADE"), nullable=False)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    credits_revoked = Column(Integer, default=0)
    reason = Column(Text)
    status = Column(String(20), default="PENDING")
    dodo_response = Column(JSON)
    processed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class APIKey(Base):
    __tablename__ = "api_keys"
    
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    app_name = Column(String(100), ForeignKey("app_registry.app_name", ondelete="CASCADE"), nullable=False)
    api_key = Column(String(255), unique=True, nullable=False)
    api_key_prefix = Column(String(20), nullable=False)
    key_type = Column(String(10), default="LIVE")
    scopes = Column(JSON)
    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime)
    expires_at = Column(DateTime)
    created_by = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


# Database dependency
def get_db() -> Generator[Session, None, None]:
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Initialize database
def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)

